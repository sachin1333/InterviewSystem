"""Two writers race on the same (session_id, seq). One wins; the other fails.

SQLite's primary-key constraint is the enforcement mechanism. The loser
must see a :class:`ValueError` (not a SQLite-specific exception leaking
through) and the log must contain exactly the winner's row.
"""
from datetime import UTC, datetime
from threading import Barrier, Thread

from adapters.eventlog.sqlite_log import SqliteEventLog
from core.events import Envelope, SessionStarted

BASE = datetime(2026, 4, 22, 10, 0, 0, tzinfo=UTC)


def _env(seq, rubric):
    return Envelope(
        session_id="s", seq=seq, at=BASE,
        payload=SessionStarted(rubric_version=rubric),
    )


def test_concurrent_same_seq_exactly_one_winner(tmp_path):
    # Two separate SqliteEventLog connections pointing at the same file —
    # simulates two processes (or two threads with independent connections).
    log_a = SqliteEventLog(tmp_path / "log.db")
    log_b = SqliteEventLog(tmp_path / "log.db")

    barrier = Barrier(2)
    results: dict[str, Exception | Envelope] = {}

    def writer(name: str, log: SqliteEventLog, rubric: str) -> None:
        barrier.wait()
        try:
            results[name] = log.append(_env(1, rubric))
        except Exception as exc:  # capture for assertion
            results[name] = exc

    t_a = Thread(target=writer, args=("a", log_a, "A"))
    t_b = Thread(target=writer, args=("b", log_b, "B"))
    t_a.start()
    t_b.start()
    t_a.join()
    t_b.join()

    log_a.close()
    log_b.close()

    successes = [v for v in results.values() if isinstance(v, Envelope)]
    failures = [v for v in results.values() if isinstance(v, Exception)]
    assert len(successes) == 1
    assert len(failures) == 1
    assert isinstance(failures[0], ValueError)

    # Exactly one row landed, matching the winner.
    check = SqliteEventLog(tmp_path / "log.db")
    try:
        rows = check.get_session("s")
        assert len(rows) == 1
        assert rows[0].seq == 1
        winner = successes[0]
        assert rows[0].payload == winner.payload
    finally:
        check.close()


def test_sequential_writes_survive_across_connections(tmp_path):
    # Sanity: serial writes via separate connections to the same file all
    # land and retain order. No concurrency — just proves the persistence
    # path isn't connection-local.
    a = SqliteEventLog(tmp_path / "log.db")
    a.append(_env(1, "a"))
    a.close()

    b = SqliteEventLog(tmp_path / "log.db")
    b.append(_env(2, "b"))
    b.close()

    c = SqliteEventLog(tmp_path / "log.db")
    try:
        rows = c.get_session("s")
        assert [r.seq for r in rows] == [1, 2]
    finally:
        c.close()
