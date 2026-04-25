from __future__ import annotations

from core.events import Envelope


class InMemoryEventLog:
    """Append-only in-memory event log keyed by session_id.

    Guarantees:
    - per-session monotonic, positive `seq` ordering
    - `idem_key` dedup: appending an envelope whose key matches a prior event
      for the same session returns the existing envelope without writing a
      duplicate row
    """

    def __init__(self) -> None:
        self._by_session: dict[str, list[Envelope]] = {}
        # (session_id, idem_key) -> envelope
        self._by_idem: dict[tuple[str, str], Envelope] = {}

    def append(self, envelope: Envelope, idem_key: str | None = None) -> Envelope:
        if not isinstance(envelope, Envelope):
            raise TypeError("only Envelope instances can be appended")

        key = idem_key or envelope.idem_key
        sess = envelope.session_id

        if key is not None:
            existing = self._by_idem.get((sess, key))
            if existing is not None:
                return existing

        lst = self._by_session.setdefault(sess, [])
        if lst and envelope.seq <= lst[-1].seq:
            raise ValueError(
                "envelope.seq must be greater than last appended seq for the session"
            )
        lst.append(envelope)
        if key is not None:
            self._by_idem[(sess, key)] = envelope
        return envelope

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
