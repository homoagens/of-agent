# conversation.py — cross-turn conversation memory for the OF-Agent REPL.
#
# Problem: run_agent() builds a fresh message list every call, so by default
# the agent remembers nothing between REPL turns.
#
# Solution: ConversationMemory maintains a rolling list of {role, content}
# message objects — exactly the format the LLM expects — and passes them to
# run_agent() via the `initial_messages` parameter.
#
# The resulting message list sent to the LLM:
#
#   [system_prompt]
#   [SUMMARY OF PREVIOUS EXCHANGES]   <- LLM summary of old exchanges (role: user)
#   {role: assistant, content: reply n-2}
#   {role: user,      content: query n-1}
#   {role: assistant, content: reply n-1}   <- MESSAGES_RECENT verbatim
#   {role: user,      content: query n}     <- the NEW query (user_task)
#
# When history grows beyond MAX_HISTORY_MESSAGES, memory.compress() summarises
# the oldest messages into a single [SUMMARY ...] user message.

import config as _config
import memory as _memory

# Number of messages (user + assistant counted separately) kept before
# LLM compression kicks in.  8 = 4 full exchanges verbatim.
MAX_HISTORY_MESSAGES = 8

# Hard fallback: if LLM compression fails, keep only the newest messages
# rather than crashing the REPL.
FALLBACK_MESSAGES = 4


class ConversationMemory:
    """
    Rolling cross-turn conversation history as a proper LLM message list.

    Usage in the REPL loop:
        mem = ConversationMemory()
        while True:
            q = input("You > ")
            result = run_agent(cfg, user_task=q, initial_messages=mem.as_messages())
            reply = result.get("reply", "")
            mem.add(q, reply)
            print(reply)
    """

    def __init__(self):
        # Flat alternating list:
        # [{role:user, content:q1}, {role:assistant, content:r1}, ...]
        self._messages: list[dict] = []

    # ── public API ────────────────────────────────────────────────────────

    def add(self, user_input: str, agent_reply: str) -> None:
        """
        Record one exchange and compress history if it has grown too long.
        Compression is an LLM call via memory.py; on failure the oldest
        pair is simply dropped.
        """
        self._messages.append({"role": "user",      "content": user_input})
        self._messages.append({"role": "assistant", "content": agent_reply})

        if len(self._messages) > MAX_HISTORY_MESSAGES:
            self._compress()

    def as_messages(self) -> list[dict]:
        """
        Return the current history as a list of {role, content} dicts,
        ready to be passed as `initial_messages` to run_agent().
        Returns [] when empty so run_agent() behaves as if no history exists.
        """
        return list(self._messages)

    def clear(self) -> None:
        self._messages.clear()

    def __len__(self) -> int:
        """Number of complete (user+assistant) exchanges stored."""
        return len(self._messages) // 2

    # ── internals ─────────────────────────────────────────────────────────

    def _compress(self) -> None:
        try:
            self._messages = _memory.compress(
                self._messages,
                threshold=MAX_HISTORY_MESSAGES,
                label="OF-Agent conversation about an OpenFOAM simulation",
                model=_config.DEFAULT_MODEL,
            )
        except Exception as exc:
            print(f"\n[memory] compression failed ({exc}), keeping latest exchanges.")
            self._messages = self._messages[-FALLBACK_MESSAGES:]
