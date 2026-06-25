# session.py — persistent conversation memory for the OF-Agent.
#
# SessionMemory extends ConversationMemory with JSON persistence via the
# case context (LocalContext or SSHContext).  The session file lives INSIDE
# the OpenFOAM case directory as ".of_agent_session.json" so it always
# travels with the simulation regardless of how it is accessed.
#
# File format:
#   {
#     "saved_at"   : "2026-04-15 14:32:01",
#     "case"       : "simulo@host:/scratch/motorBike",
#     "n_exchanges": 7,
#     "messages"   : [ {role, content}, ... ]   <- same list as _messages
#   }

import json
from datetime import datetime

from conversation import ConversationMemory


class SessionMemory(ConversationMemory):
    """
    ConversationMemory with JSON save / load via a case context.

    The session file is always .of_agent_session.json inside the case root,
    written/read through the same LocalContext or SSHContext used everywhere
    else.  Works identically for local and remote cases.

    Usage:
        mem = SessionMemory(context=ctx, case_label="host:/path")
        # previous session is loaded automatically in __init__
        # ... REPL loop ...
        mem.save()   # call on exit (or via try/finally)
    """

    SESSION_FILE = ".of_agent_session.json"

    def __init__(self, context, case_label: str = ""):
        super().__init__()
        self._ctx        = context
        self._case_label = case_label
        self._loaded     = False
        self._load()

    # ── persistence ───────────────────────────────────────────────────────

    def _load(self) -> None:
        if not self._ctx.exists(self.SESSION_FILE):
            return
        try:
            data = json.loads(self._ctx.read_text(self.SESSION_FILE))
            msgs = data.get("messages", [])
            if not msgs:
                return
            self._messages = msgs
            self._loaded   = True
            saved_at = data.get("saved_at", "unknown date")
            n        = data.get("n_exchanges", len(msgs) // 2)
            case     = data.get("case", "")
            print(
                f"\n  [session] previous session loaded: "
                f"{n} exchanges, saved {saved_at}"
                + (f" - case: {case}" if case else "")
            )
        except Exception as exc:
            print(f"\n  [session] could not load previous session: {exc}")

    def save(self) -> None:
        """
        Write the current conversation history into the case directory.
        Silently succeeds even if there is nothing to save.
        """
        if not self._messages:
            return
        try:
            data = {
                "saved_at":    datetime.now().strftime("%Y-%m-%d %H:%M:%S"),
                "case":        self._case_label,
                "n_exchanges": len(self._messages) // 2,
                "messages":    self._messages,
            }
            self._ctx.write_text(
                self.SESSION_FILE,
                json.dumps(data, indent=2, ensure_ascii=False),
            )
            n = len(self._messages) // 2
            print(f"\n  [session] {n} exchanges saved -> "
                  f"{self._ctx.resolve_str()}/{self.SESSION_FILE}")
        except Exception as exc:
            print(f"\n  [session] could not save session: {exc}")

    def clear(self) -> None:
        """
        Wipe in-memory history AND remove the on-disk session file.
        Used by the /clear slash command.
        """
        super().clear()
        self._loaded = False
        if self._ctx.exists(self.SESSION_FILE):
            try:
                self._ctx.remove(self.SESSION_FILE)
            except Exception as exc:
                print(f"\n  [session] could not remove session file: {exc}")

    @property
    def loaded_from_file(self) -> bool:
        """True if a previous session was successfully loaded."""
        return self._loaded
