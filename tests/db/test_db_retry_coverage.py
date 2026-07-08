import sqlite3
import pytest
from app.core.db.connection import with_db_retry


def test_retries_on_locked_then_succeeds():
    calls = {"n": 0}

    @with_db_retry(max_retries=3, initial_delay=0.0)
    def flaky():
        calls["n"] += 1
        if calls["n"] < 3:
            raise sqlite3.OperationalError("database is locked")
        return "ok"

    assert flaky() == "ok"
    assert calls["n"] == 3


def test_non_lock_operationalerror_not_retried():
    calls = {"n": 0}

    @with_db_retry(max_retries=3, initial_delay=0.0)
    def boom():
        calls["n"] += 1
        raise sqlite3.OperationalError("no such table: x")

    with pytest.raises(sqlite3.OperationalError):
        boom()
    assert calls["n"] == 1  # not retried


def test_non_sqlite_error_is_passthrough():
    # simulates a postgres psycopg OperationalError: different class -> no retry
    class PgOperationalError(Exception):
        pass

    calls = {"n": 0}

    @with_db_retry(max_retries=3, initial_delay=0.0)
    def pg():
        calls["n"] += 1
        raise PgOperationalError("connection reset")

    with pytest.raises(PgOperationalError):
        pg()
    assert calls["n"] == 1  # postgres no-op contract


def test_retries_on_postgres_errors():
    pytest.importorskip("psycopg")
    import psycopg
    from psycopg import errors as pg_errors

    for err_cls in (
        psycopg.OperationalError,
        pg_errors.SerializationFailure,
        pg_errors.DeadlockDetected,
        pg_errors.LockNotAvailable,
    ):
        calls = {"n": 0}

        @with_db_retry(max_retries=3, initial_delay=0.0)
        def flaky():
            calls["n"] += 1
            if calls["n"] < 3:
                raise err_cls("mock error")
            return "ok"

        assert flaky() == "ok"
        assert calls["n"] == 3

