# memory.py — context compression for agents with long conversations.
# Transparent to the agent: takes a message list, returns a possibly
# compressed version (same structure, fewer elements).

import config
import llm_client

SYSTEM_PROMPT_SUMMARY = """You are an assistant specialised in summarising conversations.
You receive a sequence of messages and produce a compact but faithful summary.
Preserve all factual information.
Reply ONLY with the summary text — no JSON, no prefixes."""


def summarize(text, label="conversation", model=None):
    """Single LLM call to compress text. No loop."""
    messages = [
        {"role": "system", "content": SYSTEM_PROMPT_SUMMARY},
        {"role": "user",
         "content": f"Summarise this {label}, preserving all important facts:\n\n{text}"},
    ]
    return llm_client.call_llm(
        messages    = messages,
        model       = model,
        temperature = 0.2,
        max_tokens  = 2048,
    )


def compress(messages, threshold=None, label="conversation", model=None):
    """
    If the message list exceeds the threshold, compress older messages into
    a summary.  threshold=0 forces compression regardless of count (used
    for the character-based limit in agent.py).

    Always preserves:
      - the system prompt at position 0 (if present)
      - the last config.MESSAGES_RECENT messages verbatim

    Returns the compressed list, or the original if below threshold.
    """
    if threshold is None:
        threshold = config.MAX_MESSAGES
    if len(messages) <= threshold:
        return messages

    recent_n   = config.MESSAGES_RECENT
    has_system = bool(messages) and messages[0].get("role") == "system"

    if has_system:
        system_msg  = messages[0]
        to_compress = messages[1:-recent_n]
        recent      = messages[-recent_n:]
    else:
        system_msg  = None
        to_compress = messages[:-recent_n]
        recent      = messages[-recent_n:]

    if not to_compress:
        return messages

    text = "\n".join(
        f"{m['role'].upper()}: {m['content'][:500]}"
        for m in to_compress
    )

    if config.DEBUG:
        print(f"[memory] Compressing {len(to_compress)} messages ({label})...")
    summary = summarize(text, label, model=model)

    summary_msg = {
        "role":    "user",
        "content": f"[SUMMARY OF PREVIOUS EXCHANGES]:\n{summary}",
    }

    if has_system:
        return [system_msg, summary_msg] + recent
    return [summary_msg] + recent
