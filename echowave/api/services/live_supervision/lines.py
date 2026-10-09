"""Turning the tap's words into transcript lines a listener can read.

Each line has a speaker, a number and a ``final`` flag. A line is sent
again every time it grows (the caller's interim words, the agent's words
as they are spoken), with the same number, so a panel replaces it in place;
when it is final it does not change again. Every event carries ``seq``,
one counter for the whole call, so a listener that joins late can merge the
backlog with what it receives live and keep the order the call had.

Secrets are masked here, before anything leaves the call's worker, with the
same rules the stored transcript uses (``services/privacy/masking``): the
agent asking for an OTP makes the caller's next digits a code.
"""

from __future__ import annotations

from datetime import UTC, datetime
from typing import Any

from api.services.privacy.masking import ACCOUNT_WORDS, CODE_WORDS, mask_text


def _now() -> str:
    return datetime.now(UTC).isoformat(timespec="milliseconds")


class LineBuilder:
    """Stateful: one per call."""

    def __init__(self) -> None:
        self.seq = 0
        self._line = 0
        self._caller_line: int | None = None
        self._agent_line: int | None = None
        self._agent_words: list[str] = []
        self._asked_code = False
        self._asked_account = False

    def _next_seq(self) -> int:
        self.seq += 1
        return self.seq

    def _new_line(self) -> int:
        self._line += 1
        return self._line

    def event(self, kind: str, **fields: Any) -> dict[str, Any]:
        """Any other event (a whisper, the step, the end), stamped in order."""
        return {"type": kind, "seq": self._next_seq(), "at": _now(), **fields}

    def _line_event(self, speaker: str, line: int, text: str, final: bool) -> dict:
        return self.event(
            "line", speaker=speaker, line=line, text=text, final=bool(final)
        )

    def caller(self, text: str, final: bool) -> list[dict]:
        out = self._finish_agent()
        text = (text or "").strip()
        if not text:
            return out
        if self._caller_line is None:
            self._caller_line = self._new_line()
        masked = mask_text(
            text,
            asked_for_code=self._asked_code,
            asked_for_account=self._asked_account,
        )
        out.append(self._line_event("caller", self._caller_line, masked, final))
        if final:
            self._caller_line = None
        return out

    def agent_text(self, text: str) -> list[dict]:
        text = (text or "").strip()
        if not text:
            return []
        out: list[dict] = []
        if self._caller_line is not None:
            # The caller's interim words were never finalised (the agent
            # started over them); the line stays as it last read.
            self._caller_line = None
        if self._agent_line is None:
            self._agent_line = self._new_line()
            self._agent_words = []
        self._agent_words.append(text)
        joined = " ".join(self._agent_words)
        out.append(
            self._line_event("agent", self._agent_line, mask_text(joined), False)
        )
        return out

    def agent_done(self) -> list[dict]:
        return self._finish_agent()

    def interrupted(self) -> list[dict]:
        out = self._finish_agent(cut_off=True)
        out.append(self.event("interrupted"))
        return out

    def _finish_agent(self, *, cut_off: bool = False) -> list[dict]:
        if self._agent_line is None:
            return []
        joined = " ".join(self._agent_words)
        self._asked_code = bool(CODE_WORDS.search(joined))
        self._asked_account = bool(ACCOUNT_WORDS.search(joined))
        event = self._line_event("agent", self._agent_line, mask_text(joined), True)
        if cut_off:
            event["cut_off"] = True
        self._agent_line = None
        self._agent_words = []
        return [event]
