"""SQLite-backed persistent :class:`EventLog`.

Same contract as :class:`core.eventlog.InMemoryEventLog`:
- Append-only, per-session monotonic `seq`.
- Idempotency keyed on `(session_id, idem_key)`; a duplicate key returns the
  already-stored envelope without writing a new row.
- Concurrent writers racing on the same `seq` resolve via the primary-key
  constraint: the loser gets :class:`ValueError`.

Schema:

.. code-block:: sql

    CREATE TABLE events (
        session_id   TEXT NOT NULL,
        seq          INTEGER NOT NULL,
        at           TEXT NOT NULL,
        payload_type TEXT NOT NULL,
        payload_json TEXT NOT NULL,
        idem_key     TEXT,
        PRIMARY KEY (session_id, seq)
    );
    CREATE UNIQUE INDEX events_idem_uq
        ON events (session_id, idem_key)
        WHERE idem_key IS NOT NULL;

WAL mode is enabled so readers don't block writers and vice versa.
"""
from __future__ import annotations

import contextlib
import sqlite3
import threading
from datetime import datetime
from pathlib import Path
from typing import Any

from pydantic import BaseModel

from core.events import EVENT_TYPES, Envelope

_NAME_TO_TYPE: dict[str, type[BaseModel]] = {cls.__name__: cls for cls in EVENT_TYPES}

_SCHEMA = """
CREATE TABLE IF NOT EXISTS events (
    session_id   TEXT    NOT NULL,
    seq          INTEGER NOT NULL,
    at           TEXT    NOT NULL,
    payload_type TEXT    NOT NULL,
    payload_json TEXT    NOT NULL,
    idem_key     TEXT,
    PRIMARY KEY (session_id, seq)
);
CREATE UNIQUE INDEX IF NOT EXISTS events_idem_uq
    ON events (session_id, idem_key)
    WHERE idem_key IS NOT NULL;
"""


class SqliteEventLog:
    """Persistent event log backed by SQLite in WAL mode."""

    def __init__(self, path: str | Path) -> None:
        self._path = str(path)
        # check_same_thread=False so a single log can be shared across
        # threads in tests; real concurrent writes still serialize through
        # SQLite's own locking.
        self._conn = sqlite3.connect(
            self._path, isolation_level=None, check_same_thread=False
        )
        # Serialise all Python-level access so multiple threads sharing
        # one connection object don't race on transaction state.
        self._lock = threading.Lock()
        self._conn.execute("PRAGMA journal_mode = WAL")
        self._conn.execute("PRAGMA synchronous = NORMAL")
        self._conn.execute("PRAGMA foreign_keys = ON")
        self._conn.executescript(_SCHEMA)

    # --- mutation --------------------------------------------------------

    def append(self, envelope: Envelope, idem_key: str | None = None) -> Envelope:
        if not isinstance(envelope, Envelope):
            raise TypeError("only Envelope instances can be appended")

        key = idem_key or envelope.idem_key
        sess = envelope.session_id

        conn = self._conn
        with self._lock:
            try:
                conn.execute("BEGIN IMMEDIATE")
            except sqlite3.OperationalError as exc:  # pragma: no cover — unlikely
                raise RuntimeError(f"could not acquire write lock: {exc}") from exc

            try:
                if key is not None:
                    row = conn.execute(
                        "SELECT session_id, seq, at, payload_type, payload_json, idem_key "
                        "FROM events WHERE session_id = ? AND idem_key = ?",
                        (sess, key),
                    ).fetchone()
                    if row is not None:
                        conn.execute("COMMIT")
                        return _row_to_envelope(row)

                last = conn.execute(
                    "SELECT MAX(seq) FROM events WHERE session_id = ?", (sess,)
                ).fetchone()[0]
                if last is not None and envelope.seq <= last:
                    conn.execute("ROLLBACK")
                    raise ValueError(
                        "envelope.seq must be greater than last appended seq for the session"
                    )

                try:
                    conn.execute(
                        "INSERT INTO events "
                        "(session_id, seq, at, payload_type, payload_json, idem_key) "
                        "VALUES (?, ?, ?, ?, ?, ?)",
                        (
                            sess,
                            envelope.seq,
                            envelope.at.isoformat(),
                            type(envelope.payload).__name__,
                            envelope.payload.model_dump_json(),
                            key,
                        ),
                    )
                except sqlite3.IntegrityError as exc:
                    conn.execute("ROLLBACK")
                    # Could be either the (session_id, seq) PK or the idem_key
                    # unique index; both map to the same caller-visible error.
                    raise ValueError(f"duplicate event: {exc}") from exc

                conn.execute("COMMIT")
                return envelope
            except Exception:
                # Best-effort rollback. If already rolled back this is a no-op.
                with contextlib.suppress(sqlite3.OperationalError):
                    conn.execute("ROLLBACK")
                raise

    # --- queries ---------------------------------------------------------

    def get_session(self, session_id: str) -> tuple[Envelope, ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT session_id, seq, at, payload_type, payload_json, idem_key "
                "FROM events WHERE session_id = ? ORDER BY seq ASC",
                (session_id,),
            ).fetchall()
        return tuple(_row_to_envelope(r) for r in rows)

    def all(self) -> tuple[Envelope, ...]:
        with self._lock:
            rows = self._conn.execute(
                "SELECT session_id, seq, at, payload_type, payload_json, idem_key "
                "FROM events ORDER BY session_id, seq ASC"
            ).fetchall()
        return tuple(_row_to_envelope(r) for r in rows)

    def last_seq(self, session_id: str) -> int | None:
        with self._lock:
            val = self._conn.execute(
                "SELECT MAX(seq) FROM events WHERE session_id = ?", (session_id,)
            ).fetchone()[0]
        return int(val) if val is not None else None

    # --- lifecycle -------------------------------------------------------

    def close(self) -> None:
        self._conn.close()

    def __enter__(self) -> SqliteEventLog:
        return self

    def __exit__(self, *exc_info: Any) -> None:
        self.close()


def _row_to_envelope(
    row: tuple[str, int, str, str, str, str | None],
) -> Envelope:
    session_id, seq, at_iso, payload_type, payload_json, idem_key = row
    cls = _NAME_TO_TYPE.get(payload_type)
    if cls is None:
        raise ValueError(f"unknown payload type in log row: {payload_type!r}")
    payload = cls.model_validate_json(payload_json)
    return Envelope(
        session_id=session_id,
        seq=seq,
        at=datetime.fromisoformat(at_iso),
        payload=payload,
        idem_key=idem_key,
    )
