# Epic E2: DB Abstraction (SQLAlchemy engine + facade seam) — Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** Run the whole app on a SQLAlchemy engine that defaults to local sqlite and switches to Postgres via `PIXELPIVOT_DB_URL`, **without rewriting the ~125 raw-SQL call sites** — by keeping `get_connection()` as the single seam and yielding a dialect-translating compat wrapper.

**Architecture:** `connection.py` gains `get_engine()` (cached per-URL, sqlite pragmas via a `connect` event listener). `get_connection()` stops opening a bare `sqlite3.Connection` and instead yields a `_CompatConnection` wrapping `engine.raw_connection()`. A `_CompatCursor` translates `?`-paramstyle to the active dialect and normalizes row access so existing `row["col"]` code keeps working. Only `schema.py` needs dialect-aware DDL; all other consumers ride the seam untouched.

**Tech Stack:** SQLAlchemy 2.x Core (engine + `raw_connection`), `psycopg[binary]` (postgres, optional), sqlite3, pytest, docker-compose Postgres for CI.

**Spec:** `docs/superpowers/specs/2026-06-30-production-readiness-design.md` (E2 section, facade-seam revision).
**Beads:** epic `pixelpivot_batch-6w2`; children `.1` (e2.1), `.2` (e2.2), `.3` (e2.3), `.7` (e2.4). (`.4/.5/.6` closed-obsolete — seam routes those files unchanged.)

---

## File Structure

- **Modify** `app/core/db/connection.py` — add `get_engine()`, the `connect` pragma listener, `_CompatCursor`, `_CompatConnection`; rewrite `_open()`/`get_connection()` to be engine-backed. Same public surface (`get_connection`, `transaction`, `get_db_path`, `init_db` import).
- **Modify** `app/core/db/schema.py` — `_create_tables` branches DDL on `engine.dialect.name`.
- **Modify** `pyproject.toml` / requirements — add `sqlalchemy>=2.0`, optional `psycopg[binary]`.
- **Modify** `docker-compose.yml` + CI workflow — Postgres service + two-backend test matrix.
- **Create** `tests/db/test_engine_factory.py`, `tests/db/test_compat_seam.py`, `tests/db/test_schema_dialect.py`.

**Non-goal:** converting repositories/analytics/export to Core expressions (deferred, YAGNI).

---

## Task 1 (e2.1): SQLAlchemy dependency + engine factory

**Files:**
- Modify: `pyproject.toml` (deps), `app/core/db/connection.py`
- Test: `tests/db/test_engine_factory.py`

- [ ] **Step 1: Add dependency**

In `pyproject.toml` `[project] dependencies`, add `"sqlalchemy>=2.0,<3"`. Postgres driver stays optional: add an extra `[project.optional-dependencies]` `postgres = ["psycopg[binary]>=3.1"]`. Then:

```bash
pip install "sqlalchemy>=2.0,<3"
```

- [ ] **Step 2: Write the failing test**

```python
# tests/db/test_engine_factory.py
import os
from app.core.db import connection as conn


def test_engine_defaults_to_sqlite(tmp_path, monkeypatch):
    monkeypatch.delenv("PIXELPIVOT_DB_URL", raising=False)
    monkeypatch.setenv("PIXELPIVOT_DB_PATH", str(tmp_path / "t.db"))
    conn.reset_engine_cache()
    eng = conn.get_engine()
    assert eng.dialect.name == "sqlite"


def test_engine_url_override_selects_dialect(monkeypatch):
    monkeypatch.setenv("PIXELPIVOT_DB_URL", "postgresql+psycopg://u:p@localhost/x")
    conn.reset_engine_cache()
    eng = conn.get_engine()
    assert eng.dialect.name == "postgresql"
    conn.reset_engine_cache()


def test_sqlite_connection_has_wal(tmp_path, monkeypatch):
    monkeypatch.delenv("PIXELPIVOT_DB_URL", raising=False)
    monkeypatch.setenv("PIXELPIVOT_DB_PATH", str(tmp_path / "wal.db"))
    conn.reset_engine_cache()
    raw = conn.get_engine().raw_connection()
    try:
        cur = raw.cursor()
        cur.execute("PRAGMA journal_mode")
        assert cur.fetchone()[0].lower() == "wal"
    finally:
        raw.close()
```

- [ ] **Step 3: Run test to verify it fails**

Run: `pytest tests/db/test_engine_factory.py -v`
Expected: FAIL — `get_engine`/`reset_engine_cache` not defined.

- [ ] **Step 4: Implement the engine factory**

Add to `app/core/db/connection.py` (imports: `from sqlalchemy import create_engine, event`; `from sqlalchemy.engine import Engine`):

```python
_engines: dict[str, "Engine"] = {}


def _db_url() -> str:
    """Resolve the SQLAlchemy URL: explicit PIXELPIVOT_DB_URL wins, else sqlite at get_db_path()."""
    import os
    url = os.getenv("PIXELPIVOT_DB_URL")
    if url:
        return url
    return f"sqlite:///{get_db_path()}"


def get_engine() -> Engine:
    """Return a process-cached engine for the current DB URL."""
    url = _db_url()
    eng = _engines.get(url)
    if eng is None:
        is_sqlite = url.startswith("sqlite")
        if is_sqlite:
            get_db_path().parent.mkdir(parents=True, exist_ok=True)
        eng = create_engine(
            url,
            future=True,
            # one shared in-process connection for file sqlite is fine; pool for pg
            connect_args={"check_same_thread": False} if is_sqlite else {},
        )
        _engines[url] = eng
    return eng


def reset_engine_cache() -> None:
    """Dispose + drop cached engines (test isolation across DB paths)."""
    for eng in _engines.values():
        eng.dispose()
    _engines.clear()


@event.listens_for(Engine, "connect")
def _apply_sqlite_pragmas(dbapi_connection, connection_record):
    """WAL + project pragmas on every sqlite connection from any engine in the pool."""
    if dbapi_connection.__class__.__module__.startswith("sqlite3"):
        cur = dbapi_connection.cursor()
        try:
            cur.execute("PRAGMA journal_mode=WAL")
            cur.execute("PRAGMA synchronous=NORMAL")
            cur.execute("PRAGMA busy_timeout=5000")
            cur.execute("PRAGMA foreign_keys=ON")
        finally:
            cur.close()
```

> The listener checks the dbapi module rather than dialect so it stays a no-op for psycopg connections. The legacy `_PRAGMAS`/`_configure` can stay for now (still used by any direct `_open` callers) but the listener is the source of truth.

- [ ] **Step 5: Run test to verify it passes**

Run: `pytest tests/db/test_engine_factory.py -v`
Expected: PASS (3 tests).

- [ ] **Step 6: Commit**

```bash
git add pyproject.toml app/core/db/connection.py tests/db/test_engine_factory.py
git commit -m "feat(db): SQLAlchemy engine factory + sqlite pragma event listener (e2.1)"
```

---

## Task 2 (e2.2): compat wrapper + get_connection swap

**Files:**
- Modify: `app/core/db/connection.py` (`_CompatCursor`, `_CompatConnection`, `get_connection`)
- Test: `tests/db/test_compat_seam.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/db/test_compat_seam.py
from app.core.db import connection as conn
from app.core.db.schema import init_db


def _fresh(tmp_path, monkeypatch):
    monkeypatch.delenv("PIXELPIVOT_DB_URL", raising=False)
    monkeypatch.setenv("PIXELPIVOT_DB_PATH", str(tmp_path / "seam.db"))
    conn.reset_engine_cache()
    init_db()


def test_qmark_param_and_named_row_access(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    with conn.get_connection() as c:
        cur = c.cursor()
        cur.execute(
            "INSERT INTO batch_runs (source_dir, target_dir, target_format, tool, status) "
            "VALUES (?, ?, ?, ?, ?)",
            ("src", "dst", "avif", "ffmpeg", "running"),
        )
        rid = cur.lastrowid
    with conn.get_connection() as c:
        cur = c.cursor()
        cur.execute("SELECT * FROM batch_runs WHERE id = ?", (rid,))
        row = cur.fetchone()
        assert row["source_dir"] == "src"      # named access preserved
        assert row["status"] == "running"


def test_commit_and_rollback(tmp_path, monkeypatch):
    _fresh(tmp_path, monkeypatch)
    try:
        with conn.get_connection() as c:
            c.cursor().execute(
                "INSERT INTO batch_runs (source_dir, target_dir, target_format, tool, status) "
                "VALUES (?,?,?,?,?)", ("a", "b", "webp", "vips", "running"))
            raise RuntimeError("boom")
    except RuntimeError:
        pass
    with conn.get_connection() as c:
        cur = c.cursor()
        cur.execute("SELECT COUNT(*) AS n FROM batch_runs")
        assert cur.fetchone()["n"] == 0   # rolled back
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/db/test_compat_seam.py -v`
Expected: FAIL — `get_connection` still returns a bare sqlite3 connection (passes today on sqlite, but will be the regression guard once the seam is engine-backed). If it passes now, proceed; the binding check is that it STILL passes after Step 3.

- [ ] **Step 3: Implement the compat wrapper**

Add to `app/core/db/connection.py`:

```python
class _CompatCursor:
    """Wrap a DBAPI cursor: translate ?-paramstyle to the active dialect; keep row['col']."""

    def __init__(self, dbapi_cursor, paramstyle: str):
        self._cur = dbapi_cursor
        self._paramstyle = paramstyle

    def execute(self, sql: str, params=()):
        if self._paramstyle != "qmark" and "?" in sql:
            sql = sql.replace("?", "%s")
        return self._cur.execute(sql, params or ())

    def executemany(self, sql: str, seq):
        if self._paramstyle != "qmark" and "?" in sql:
            sql = sql.replace("?", "%s")
        return self._cur.executemany(sql, seq)

    def fetchone(self):
        return self._cur.fetchone()

    def fetchall(self):
        return self._cur.fetchall()

    @property
    def lastrowid(self):
        return self._cur.lastrowid

    @property
    def rowcount(self):
        return self._cur.rowcount

    def close(self):
        self._cur.close()


class _CompatConnection:
    """Legacy-shaped wrapper over a SQLAlchemy raw DBAPI connection."""

    def __init__(self, raw, paramstyle: str):
        self._raw = raw
        self._paramstyle = paramstyle

    def cursor(self) -> _CompatCursor:
        return _CompatCursor(self._raw.cursor(), self._paramstyle)

    def execute(self, sql, params=()):
        cur = self.cursor()
        cur.execute(sql, params)
        return cur

    def commit(self):
        self._raw.commit()

    def rollback(self):
        self._raw.rollback()

    def close(self):
        self._raw.close()
```

Rewrite `get_connection()` to yield `_CompatConnection` (keep the thread-local reuse + outermost-block transaction logic; swap `_open()` for `engine.raw_connection()`):

```python
@contextlib.contextmanager
def get_connection() -> Iterator["_CompatConnection"]:
    engine = get_engine()
    paramstyle = engine.dialect.paramstyle  # 'qmark' for sqlite, 'pyformat' for psycopg
    current_path = get_db_path()
    if (not hasattr(_local, "conn") or _local.conn is None
            or getattr(_local, "conn_path", None) != current_path):
        if getattr(_local, "conn", None) is not None:
            try: _local.conn.close()
            except Exception: pass
        raw = engine.raw_connection()
        # psycopg dict rows so row['col'] keeps working
        if paramstyle != "qmark":
            try:
                from psycopg.rows import dict_row
                raw.cursor_factory = dict_row  # type: ignore[attr-defined]
            except Exception:
                pass
        c = _CompatConnection(raw, paramstyle)
        _local.conn = c
        _local.conn_path = current_path
        _local.depth = 1
        try:
            yield c
            c.commit()
        except Exception:
            try: c.rollback()
            except Exception as e: log.debug("rollback suppressed: %s", e)
            raise
        finally:
            _local.depth -= 1
            if _local.depth <= 0:
                try: c.close()
                except Exception as e: log.debug("close suppressed: %s", e)
                _local.conn = None; _local.conn_path = None; _local.depth = 0
    else:
        _local.depth += 1
        try:
            yield _local.conn
        finally:
            _local.depth -= 1
```

> sqlite still yields `sqlite3.Row` (named access already works). For postgres, `dict_row` gives the same `row["col"]`. `executescript` is NOT on the wrapper — only `schema.py` uses it and Task 3 replaces it.

- [ ] **Step 4: Run the seam tests + the broad DB suite**

Run: `pytest tests/db/test_compat_seam.py tests/ -k "db or repo or batch or analytics or telemetry" -q`
Expected: PASS — consumers unchanged, all green on sqlite.

- [ ] **Step 5: Commit**

```bash
git add app/core/db/connection.py tests/db/test_compat_seam.py
git commit -m "feat(db): facade compat wrapper; get_connection engine-backed (e2.2)"
```

---

## Task 3 (e2.3): dialect-aware schema DDL

**Files:**
- Modify: `app/core/db/schema.py` (`_create_tables`, `init_db`)
- Test: `tests/db/test_schema_dialect.py`

- [ ] **Step 1: Write the failing test**

```python
# tests/db/test_schema_dialect.py
from app.core.db import connection as conn
from app.core.db.schema import init_db, EXPECTED_TABLES


def test_sqlite_schema_creates_all_tables(tmp_path, monkeypatch):
    monkeypatch.delenv("PIXELPIVOT_DB_URL", raising=False)
    monkeypatch.setenv("PIXELPIVOT_DB_PATH", str(tmp_path / "schema.db"))
    conn.reset_engine_cache()
    init_db()
    with conn.get_connection() as c:
        cur = c.cursor()
        cur.execute("SELECT name FROM sqlite_master WHERE type='table'")
        names = {r["name"] for r in cur.fetchall()}
    assert EXPECTED_TABLES <= names


def test_pg_ddl_uses_serial_not_autoincrement():
    from app.core.db.schema import _ddl_for
    ddl = _ddl_for("postgresql")
    assert "AUTOINCREMENT" not in ddl.upper()
    assert "SERIAL" in ddl.upper() or "GENERATED" in ddl.upper()
```

- [ ] **Step 2: Run test to verify it fails**

Run: `pytest tests/db/test_schema_dialect.py -v`
Expected: FAIL — `EXPECTED_TABLES` / `_ddl_for` not defined.

- [ ] **Step 3: Implement dialect-aware DDL**

In `app/core/db/schema.py`, define the table-name set and split DDL by dialect. Keep the existing sqlite `CREATE TABLE` script as `_SQLITE_DDL`; add `_POSTGRES_DDL` (same tables, `SERIAL PRIMARY KEY` instead of `INTEGER PRIMARY KEY AUTOINCREMENT`, `TIMESTAMP`/`BOOLEAN` types):

```python
EXPECTED_TABLES = {
    "batch_runs", "batch_summary", "batch_errors", "batch_telemetry",
    "calibration_results", "images", "conversions", "metrics", "quality_priors",
    "pipeline_runs", "pipeline_logs", "pipeline_telemetry", "infra_config", "users",
}

# _SQLITE_DDL = "<existing executescript string>"
# _POSTGRES_DDL = "<same tables, SERIAL PRIMARY KEY, no AUTOINCREMENT>"

def _ddl_for(dialect_name: str) -> str:
    return _POSTGRES_DDL if dialect_name.startswith("postgres") else _SQLITE_DDL
```

Rewrite `_create_tables` to execute statement-by-statement (portable; `executescript` is sqlite-only):

```python
def _create_tables(conn) -> None:
    from .connection import get_engine
    dialect = get_engine().dialect.name
    ddl = _ddl_for(dialect)
    cur = conn.cursor()
    try:
        for stmt in [s for s in ddl.split(";") if s.strip()]:
            cur.execute(stmt)
    finally:
        cur.close()
    conn.commit()
```

> Keep `init_db()`'s existing `with get_connection() as connection: _create_tables(connection)` shape so its public contract is unchanged.

- [ ] **Step 4: Run test to verify it passes**

Run: `pytest tests/db/test_schema_dialect.py -v`
Expected: PASS.

- [ ] **Step 5: Commit**

```bash
git add app/core/db/schema.py tests/db/test_schema_dialect.py
git commit -m "feat(db): dialect-aware schema DDL, statement-wise create (e2.3)"
```

---

## Task 4 (e2.4): Postgres CI lane + dialect-leak fixes

**Files:**
- Modify: `docker-compose.yml`, CI workflow (`.github/workflows/*.yml` if present, else document `make test-pg`)
- Modify: any file with a sqlite-only SQL idiom the matrix surfaces

- [ ] **Step 1: Add a Postgres service for tests**

In `docker-compose.yml` add:

```yaml
  test-postgres:
    image: postgres:16
    environment:
      POSTGRES_USER: pixelpivot
      POSTGRES_PASSWORD: pixelpivot
      POSTGRES_DB: pixelpivot_test
    ports: ["5433:5432"]
    tmpfs: ["/var/lib/postgresql/data"]   # ephemeral, fast
```

- [ ] **Step 2: Run the suite against Postgres**

```bash
pip install "psycopg[binary]>=3.1"
# start db: docker compose up -d test-postgres
$env:PIXELPIVOT_DB_URL = "postgresql+psycopg://pixelpivot:pixelpivot@localhost:5433/pixelpivot_test"
pytest -q
```

Expected: most pass; a handful FAIL on sqlite-only idioms.

- [ ] **Step 3: Fix dialect leaks (driven by failures)**

For each failure, apply the portable form. Likely culprits and fixes:
- `INSERT OR IGNORE INTO t ...` → emit `INSERT INTO t ... ON CONFLICT DO NOTHING` when dialect is postgres (branch where the statement is built).
- `strftime('%Y-...', col)` → `to_char(col, 'YYYY-...')` on postgres.
- Boolean stored as `0/1` compared to `IS TRUE` — keep integer columns or branch.

Each fix gets a focused regression test asserting the query returns correct rows on both backends. Commit per fix:

```bash
git add <files> tests/db/<test>.py
git commit -m "fix(db): portable <idiom> for postgres dialect (e2.4)"
```

- [ ] **Step 4: Wire CI matrix**

If `.github/workflows/` exists, add a job dimension `db: [sqlite, postgres]` that sets `PIXELPIVOT_DB_URL` for the postgres leg with a `services: postgres:` container. Otherwise add a `make test-pg` target documenting the two-backend run in `CLAUDE.md`.

- [ ] **Step 5: Full suite both backends**

Run sqlite: `pytest -q`
Run postgres: `$env:PIXELPIVOT_DB_URL="postgresql+psycopg://pixelpivot:pixelpivot@localhost:5433/pixelpivot_test"; pytest -q`
Expected: both green (pre-existing streamlit-guard failures excepted).

- [ ] **Step 6: Commit + close epic**

```bash
git add docker-compose.yml .github CLAUDE.md
git commit -m "ci(db): postgres test lane + two-backend matrix (e2.4)"
bd close pixelpivot_batch-6w2.1 pixelpivot_batch-6w2.2 pixelpivot_batch-6w2.3 pixelpivot_batch-6w2.7 pixelpivot_batch-6w2
```

---

## Self-Review

**Spec coverage (E2 facade revision):**
- e2.1 engine factory + URL + sqlite pragma listener → Task 1. ✓
- e2.2 `_CompatConnection`/`_CompatCursor` paramstyle + row access + `get_connection` swap → Task 2. ✓
- e2.3 dialect-aware schema DDL, statement-wise → Task 3. ✓
- e2.4 postgres CI + dialect-leak fixes → Task 4. ✓
- Deferred (repositories/analytics/export Core port) — explicitly out of scope, routed unchanged through the seam. ✓

**Placeholder scan:** Task 3 leaves `_SQLITE_DDL`/`_POSTGRES_DDL` string bodies as references to the existing schema text — the engineer copies the current `CREATE TABLE` block verbatim into `_SQLITE_DDL` and adapts ids for `_POSTGRES_DDL`; the table list (`EXPECTED_TABLES`) is concrete. Task 4 Step 3 fixes are failure-driven (the exact set can't be known until the matrix runs) but each named idiom has a concrete portable replacement.

**Type/name consistency:** `get_engine`, `reset_engine_cache`, `_db_url`, `_CompatCursor`, `_CompatConnection`, `_ddl_for`, `EXPECTED_TABLES` — used identically across tasks. `get_connection()` keeps its name + context-manager contract.

**Risk note:** the highest-risk step is Task 2 (every consumer rides it). Its acceptance is the *full existing suite green on sqlite* — if anything regresses there, stop and fix before Task 3. Postgres correctness is proven only in Task 4.
