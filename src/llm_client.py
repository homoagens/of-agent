# llm_client.py — HTTP client for the LLM backend. Domain-agnostic.

import json
import time
import requests

import config


def _chat_completions_url() -> str:
    """
    Build the OpenAI-compatible chat-completions endpoint from BACKEND_URL.

    Accepts whatever the user configured and normalises it:
      http://host:11434              -> http://host:11434/v1/chat/completions
      http://host:11434/v1           -> http://host:11434/v1/chat/completions
      https://api.openai.com/v1      -> https://api.openai.com/v1/chat/completions
      http://host/v1/chat/completions-> used as-is
    """
    base = config.BACKEND_URL.rstrip("/")
    if base.endswith("/chat/completions"):
        return base
    if base.endswith("/v1"):
        return base + "/chat/completions"
    return base + "/v1/chat/completions"


def _build_payload(messages, model, temperature, max_tokens, stream=False):
    """Common request body, including anti-repetition penalties when enabled."""
    payload = {
        "messages":    messages,
        "model":       model,
        "temperature": temperature,
        "max_tokens":  max_tokens,
    }
    if stream:
        payload["stream"] = True
    # Only send penalties when non-zero so backends that don't support them are
    # left untouched at their defaults.
    if config.FREQUENCY_PENALTY:
        payload["frequency_penalty"] = config.FREQUENCY_PENALTY
    if config.PRESENCE_PENALTY:
        payload["presence_penalty"] = config.PRESENCE_PENALTY
    return payload


def call_llm(messages, model=None, temperature=None, max_tokens=None, timeout=None):
    """
    Send messages to the model and return the response as a string.

    messages    : list of {role, content} dicts (OpenAI format)
    model       : model name (default: config.DEFAULT_MODEL)
    temperature : default: config.DEFAULT_TEMPERATURE
    max_tokens  : default: config.MAX_TOKENS
    timeout     : request timeout in seconds (default: config.TIMEOUT)

    Automatic retry on 502 (server overloaded or cold-starting):
    backoff 30/60/90/120 s, then raises.
    """
    if model       is None: model       = config.DEFAULT_MODEL
    if temperature is None: temperature = config.DEFAULT_TEMPERATURE
    if max_tokens  is None: max_tokens  = config.MAX_TOKENS
    if timeout     is None: timeout     = config.TIMEOUT

    payload = _build_payload(messages, model, temperature, max_tokens)
    headers = {
        "Content-Type":  "application/json",
        "Authorization": f"Bearer {config.BACKEND_KEY}",
    }

    url = _chat_completions_url()
    for attempt in range(5):
        response = requests.post(
            url,
            headers=headers,
            json=payload,
            timeout=timeout,
        )
        if response.status_code != 502:
            break
        wait = 30 * (attempt + 1)
        print(f"[llm_client] 502 — waiting {wait}s, retrying ({attempt+1}/5)...")
        time.sleep(wait)

    response.raise_for_status()

    data   = response.json()
    choice = data["choices"][0]
    msg    = choice["message"]
    finish = choice.get("finish_reason", "")

    # "content" = final reply; "reasoning_content" = internal thinking (some models).
    text = (msg.get("content") or msg.get("reasoning_content") or "").strip()

    if finish == "length":
        raise RuntimeError(
            f"Response truncated (finish_reason=length). Increase MAX_TOKENS. "
            f"Partial text: {text[:100]!r}"
        )
    if not text:
        raise RuntimeError("Model returned an empty response.")

    return text


def call_llm_stream(messages, model=None, temperature=None, max_tokens=None,
                    timeout=None, on_token=None):
    """
    Streaming variant of call_llm.

    Sends the request with "stream": true and consumes the OpenAI-compatible
    Server-Sent-Events response. For each chunk it calls:

        on_token(channel, text)

    where channel is "thinking" (delta.reasoning_content — the model's internal
    reasoning, emitted by thinking models) or "answer" (delta.content — the
    actual JSON answer). Returns the full accumulated "answer" text, exactly
    like call_llm, so the caller can parse it as JSON.

    If the backend does not support streaming (or anything goes wrong mid-stream
    before any answer text arrives), raises — the caller should fall back to the
    non-streaming call_llm.
    """
    if model       is None: model       = config.DEFAULT_MODEL
    if temperature is None: temperature = config.DEFAULT_TEMPERATURE
    if max_tokens  is None: max_tokens  = config.MAX_TOKENS
    if timeout     is None: timeout     = config.TIMEOUT

    payload = _build_payload(messages, model, temperature, max_tokens, stream=True)
    headers = {
        "Content-Type":  "application/json",
        "Authorization": f"Bearer {config.BACKEND_KEY}",
    }

    answer_parts: list[str] = []
    finish = ""
    saw_sse = False

    with requests.post(_chat_completions_url(), headers=headers, json=payload,
                       timeout=timeout, stream=True) as response:
        response.raise_for_status()
        for raw in response.iter_lines(decode_unicode=True):
            if not raw:
                continue
            if not raw.startswith("data:"):
                continue
            saw_sse = True
            data = raw[len("data:"):].strip()
            if data == "[DONE]":
                break
            try:
                chunk = json.loads(data)
            except ValueError:
                continue
            choices = chunk.get("choices") or [{}]
            choice = choices[0]
            delta = choice.get("delta") or {}

            reasoning = delta.get("reasoning_content")
            if reasoning and on_token:
                on_token("thinking", reasoning)

            content = delta.get("content")
            if content:
                answer_parts.append(content)
                if on_token:
                    on_token("answer", content)

            if choice.get("finish_reason"):
                finish = choice["finish_reason"]

    if not saw_sse:
        raise RuntimeError("backend did not return a streaming (SSE) response")

    text = "".join(answer_parts).strip()
    if finish == "length":
        raise RuntimeError(
            f"Response truncated (finish_reason=length). Increase MAX_TOKENS. "
            f"Partial text: {text[:100]!r}"
        )
    if not text:
        raise RuntimeError("Model returned an empty response.")
    return text


if __name__ == "__main__":
    test_messages = [
        {"role": "system", "content": "You are a concise assistant."},
        {"role": "user",   "content": "Reply only with: CONNECTION OK"},
    ]
    try:
        r = call_llm(test_messages, temperature=0.0, max_tokens=512)
        print(f"PASS — {r}")
    except Exception as e:
        print(f"FAIL — {e}")
