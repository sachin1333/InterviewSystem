from __future__ import annotations

from core.events import Envelope


class InMemoryEventLog:
    """Simple append-only in-memory event log keyed by session_id.

    Guarantees per-session monotonic, positive `seq` ordering.
    """

    def __init__(self) -> None:
        self._by_session: dict[str, list[Envelope]] = {}

    def append(self, envelope: Envelope) -> None:
        if not isinstance(envelope, Envelope):
            raise TypeError("only Envelope instances can be appended")
        sess = envelope.session_id
        lst = self._by_session.setdefault(sess, [])
        if lst and envelope.seq <= lst[-1].seq:
            raise ValueError("envelope.seq must be greater than last appended seq for the session")
        lst.append(envelope)

    def get_session(self, session_id: str) -> tuple[Envelope, ...]:
        return tuple(self._by_session.get(session_id, []))

    def all(self) -> tuple[Envelope, ...]:
        out: list[Envelope] = []
        for lst in self._by_session.values():
            out.extend(lst)
        return tuple(out)

    def last_seq(self, session_id: str) -> int | None:
        lst = self._by_session.get(session_id)
        if not lst:
            return None
        return lst[-1].seq
