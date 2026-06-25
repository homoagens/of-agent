# llm_client.py — HTTP client for the LLM backend. Domain-agnostic.

import time
import requests

import config


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

    payload = {
        "messages":    messages,
        "model":       model,
        "temperature": temperature,
        "max_tokens":  max_tokens,
    }
    headers = {
        "Content-Type":  "application/json",
        "Authorization": f"Bearer {config.BACKEND_KEY}",
    }

    for attempt in range(5):
        response = requests.post(
            f"{config.BACKEND_URL}/llm",
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
    msg    = data["raw"]["choices"][0]["message"]
    finish = data["raw"]["choices"][0].get("finish_reason", "")

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
