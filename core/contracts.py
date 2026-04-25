from __future__ import annotations

from collections.abc import Iterable
from typing import Protocol, runtime_checkable

from core import domain
from core.events import Envelope


@runtime_checkable
class EventLog(Protocol):
    """Append-only per-session event log. Sole source of truth for session state."""

    def append(self, envelope: Envelope, idem_key: str | None = None) -> Envelope:
        """Append an event. If `idem_key` is provided and an event with the same
        key already exists for the session, return the existing envelope without
        writing a duplicate.
        """

    def get_session(self, session_id: str) -> tuple[Envelope, ...]:
        """Return all envelopes for `session_id` in seq order."""

    def last_seq(self, session_id: str) -> int | None:
        """Return the last seq for `session_id`, or None if empty."""

    def all(self) -> tuple[Envelope, ...]:
        """Return all envelopes across sessions. Order across sessions is unspecified."""


@runtime_checkable
class Challenger(Protocol):
    """Responsible for proposing next prompts/questions for a session."""

    def propose_prompts(self, session_id: str) -> Iterable[str]:
        """Return an iterable of prompt strings to ask the candidate."""


@runtime_checkable
class Examiner(Protocol):
    """Reviews turns and can produce probes or corrections."""

    def review_turn(self, session_id: str, turn: domain.Turn) -> None:
        """Inspect a Turn; may record state or schedule follow-ups."""


@runtime_checkable
class Scorer(Protocol):
    """Scores artifacts for a single dimension and emits Signals."""

    def score(self, session_id: str, artifact: domain.Artifact, dimension: domain.Dimension) -> domain.Signal:
        """Produce a Signal for `artifact` on `dimension`."""


@runtime_checkable
class Runtime(Protocol):
    """Executes user code/notebook cells in an isolated runtime."""

    def execute(self, session_id: str, code: str, timeout_seconds: float | None = None) -> str:
        """Execute `code` and return stdout/stderr or a serialized result."""


@runtime_checkable
class UIAdapter(Protocol):
    """Adapter between system and an external UI (chat, terminal, web)."""

    def send_message(self, session_id: str, actor: str, message: str) -> None:
        """Send a message to the UI for `actor`."""

    def receive_input(self, session_id: str) -> str | None:
        """Optionally receive input/response from the UI; return None if none available."""
