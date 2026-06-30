# PixelPivot Batch — Production Readiness Roadmap

**Date:** 2026-06-30
**Status:** Approved design → bead creation
**Source brainstorm:** `get_ready_for_production_20260630.md`

## Deployment Target (the constraint that drives everything)

Ships to **both air-gapped AND networked** customers from one codebase. Therefore:

- **No hard network dependency** anywhere. Every external integration is opt-in via env var, default off / local-only.
- **Graceful degradation:** JSON logs to stdout always; `/metrics` exposed but tolerates no scraper; OpenTelemetry off by default with lazy import; DB defaults to local sqlite.

### Explicitly cut from the source brainstorm
- Celery / Redis / RabbitMQ broker — reintroduces the network dependency we forbid; single-host in-process queue covers it.
- Multi-node horizontal workers.
- Sharp daemon as its own *container/node* (keep the daemon, in-process socket).
- Redis in-mem — no broker, no cross-process shared state needed on single host. Door left open via the same env-URL pattern if multi-node ever happens.

## Epics (dependency-ordered)

```
E1 Structured Logging + Tracing   (foundation, additive)
   → E2 DB Abstraction (SQLAlchemy Core)   (foundation refactor)
      → E3 Error Handling + Resilience
         → E4 Health + Graceful Shutdown
            → E5 Telemetry + Dynamic Queue   (most additive, last)
```

**Rationale:** E1 threads `trace_id` through every later epic's logs (near-zero risk, do first). E2 is the heaviest refactor — land while the call surface is small; it dissolves the `database is locked` item on the postgres path and unblocks E3's backoff (wraps the engine). E3 needs E2's engine. E4 readiness probes DB (cleaner post-E2). E5 is purely additive, safe last.

### Cross-cutting env conventions
All toggles default to air-gapped-safe (feature off / local-only):
`PIXELPIVOT_DB_URL` (default `sqlite:///./data/pixelpivot.db`), `PIXELPIVOT_LOG_FORMAT=json|text` (text default for dev TTY), `PIXELPIVOT_METRICS_ENABLED`, `PIXELPIVOT_OTEL_ENABLED=0`.

---

## E1 — Structured Logging + Tracing

- **e1.1 trace_id contextvar** — generated at API boundary (`routes.py` dependency), stored in `contextvars`, auto-injected into every log record via a logging filter. **Non-web entry points (HotFolder watcher thread, CLI) also trigger batches** — the filter must fallback-generate a trace_id (prefixed `system-`/`hotfolder-`/`cli-` UUID) when the contextvar is unset, so no log line ever raises `LookupError` or emits an empty trace.id. *Accept:* one batch request → all its log lines share trace_id; a hotfolder-triggered batch → log lines share a `hotfolder-` trace_id.
- **e1.2 ECS JSON formatter** in `logger.py` — `@timestamp`, `log.level`, `service.name`, `trace.id`, `batch.run_id/tool/format`, `performance.*`. Toggle `PIXELPIVOT_LOG_FORMAT`. *Accept:* json mode emits single-line valid JSON with ECS keys; text mode unchanged for dev.
- **e1.3 subprocess output wrapping** — ffmpeg/mogrify stderr captured, parsed, nested under `subprocess.raw_output`/`subprocess.error`; no raw multiline dump to stdout. *Accept:* failing convert → one JSON log line, raw text inside payload.
- **e1.4 propagate trace_id across worker + subprocess boundaries** — workers are `ThreadPoolExecutor` threads, which do **not** auto-copy `contextvars`. Capture trace_id at submit time (`copy_context().run(...)` or explicit injection) so worker-thread log records carry it. Cross *process* boundaries: ffmpeg via log prefix; Sharp daemon via a `trace_id` field in the TCP request frame. *Accept:* worker-thread log line and sharp-daemon log line both carry the originating request's trace_id.

## E2 — DB Abstraction (SQLAlchemy engine + facade seam)

**Revised after planning discovery (2026-06-30):** the raw-`sqlite3` surface is **~125 call sites across ~15 files** (incl. the whole `app/core/db/repositories/` dir, `queue_manager`, `calibration_runner`, `heuristic.py`, `core/telemetry.py`, `logger.DBLogHandler`), not the 4 files originally listed. A file-by-file Core port is 3x the work and risk. **Decision: facade seam** — keep `get_connection()` as the single chokepoint, put a SQLAlchemy engine underneath, and yield a thin compat wrapper so the ~15 consumers stay almost unchanged. Postgres portability comes from translating paramstyle + row access in one place.

- **e2.1 deps + engine factory** — add SQLAlchemy, vendor wheels (cp314 win + linux for air-gap mirror). `get_engine()` reads `PIXELPIVOT_DB_URL` (default `sqlite:///<get_db_path()>`), cached per-URL (test isolation). SQLite pragmas (`journal_mode=WAL`, `synchronous=NORMAL`, `busy_timeout=5000`, `foreign_keys=ON`) applied via `@event.listens_for(Engine, "connect")` gated on the sqlite dialect — never one-off — so every pooled/recycled connection is consistent. *Accept:* engine builds for both sqlite and postgres URLs; a fresh sqlite connection reports `journal_mode=wal`.
- **e2.2 facade compat wrapper + get_connection swap** — `get_connection()` yields a `_CompatConnection` backed by `engine.raw_connection()`, preserving the legacy API: `.cursor()`, `.commit()/.rollback()/.close()`, thread-local reuse + outermost-block transaction semantics. `_CompatCursor.execute()` translates `?`→dialect paramstyle (no-op on sqlite, `?`→`%s` on postgres) and rows expose `row["col"]` (sqlite `Row`; psycopg `dict_row`). *Accept:* full existing suite green on sqlite with consumers unchanged.
- **e2.3 dialect-aware schema DDL** — `schema.py` is the one consumer that can't be made portable by the seam alone (DDL differs: `INTEGER PRIMARY KEY AUTOINCREMENT` vs `SERIAL`/`IDENTITY`, `executescript` unsupported on psycopg). Branch DDL on `engine.dialect.name`; sqlite keeps current script, postgres gets an equivalent. `init_db()` contract preserved. *Accept:* schema init green on both dialects; table set identical.
- **e2.4 postgres CI lane + smoke** — compose Postgres service + test matrix runs the suite on both backends; fix any dialect leaks the facade missed (e.g. `INSERT OR IGNORE`, `strftime`, boolean `0/1`). *Accept:* suite green sqlite + postgres.

> Deferred (not blocking E2): repositories etc. keep raw SQL through the seam. A later optional epic may migrate hot queries to Core expressions, but YAGNI for now.

## E3 — Error Handling + Resilience

- **e3.1 SQLite backoff-retry decorator** — wraps engine exec. Catches **`sqlalchemy.exc.OperationalError`** and inspects the underlying driver error (string/SQLite code contains `database is locked`) so it does **not** retry generic Postgres connection dropouts. Exponential, capped. No-op when dialect is postgres. *Accept:* simulated sqlite lock → retries then succeeds; postgres OperationalError → not retried.
- **e3.2 DLQ** — corrupt / repeatedly-failing file → moved to `corrupt_or_failed/` subdir + `conversions.status='dlq'` flag + diagnostic log; batch continues. *Accept:* one corrupt file in a batch → isolated, rest succeed, flagged in DB.
- **e3.3 Sharp→Vips fallback** — on daemon timeout/disconnect, single retry via `VipsConverter`. On successful fallback save, **override the `tool` metadata field to `vips`** in the DB row (do not roll back / leave partial Sharp entry) so metrics history stays accurate. Logged. *Accept:* daemon killed mid-batch → batch completes via vips, DB row tool=`vips`, fallback logged.

## E4 — Health + Graceful Shutdown

- **e4.1 /healthz** — `/healthz/live` (process up) + `/healthz/ready` (DB connect, `target_dir` writable, ffmpeg/magick/sharp-socket reachable). 200/503. *Accept:* break each dependency → ready flips 503 naming the failed check.
- **e4.2 SIGTERM graceful shutdown** — handler coordinates **both** lanes: stop `HotFolderManager` watcher loop (no new debounced batches) **and** `BatchOrchestrator` (finish active chunk, mark run status, flush, exit ≤ grace window). **Subprocess reaping:** maintain a thread-safe registry of active `ffmpeg`/`mogrify` `Popen` handles; on signal, after the grace window `terminate()` (then `kill()`) any still-running children before joining threads, so none orphan/zombie holding FDs or leaving partial files. No half-written outputs / orphan temp. *Accept:* SIGTERM mid-batch → current chunk completes, hotfolder watcher stops, no orphan ffmpeg/mogrify process survives, DB status consistent, no orphan temp files.

## E5 — Telemetry + Dynamic Queue

- **e5.1 /metrics Prometheus** (`prometheus_client`) — `pixelpivot_jobs_total{status,tool,format}`, `pixelpivot_processing_seconds`, `pixelpivot_queue_depth`, `pixelpivot_compression_ratio`. Toggle `PIXELPIVOT_METRICS_ENABLED`; tolerates no scraper. *Accept:* endpoint scrapeable, counters move on a batch.
- **e5.2 resource-aware chunk sizing** — chunk by avg megapixel + RAM/CPU pressure instead of static limits. Deterministic heuristic for testing: **expected peak RAM ≈ `4 × megapixels × chunk_size`** bytes (W×H×4 raw RGBA per in-flight image). Bounded by existing `FFMPEG_BATCH_MAX_*` ceilings. *Accept:* high-MP batch → smaller chunk than low-MP batch given same RAM budget, per the formula; never exceeds existing ceilings.
- **e5.3 disk-% backpressure** — pause chunk pickup when target volume usage > threshold (e.g. 90%); resume when freed. Probe with `shutil.disk_usage(os.path.abspath(target_dir))` on the **resolved target_dir** (which may be a network mount / external drive / separate logical volume), never a static system root. *Accept:* simulated full disk on the target volume → pickup pauses, resumes on free; threshold reads the target_dir's volume not `C:`/`/`.
- **e5.4 priority lanes (DB-driven)** — GUI submit = high, hotfolder sync = low. Rather than an in-memory `PriorityQueue` (state lost on API crash/restart), persist a `priority` column and poll the next chunk with `ORDER BY priority DESC, created_at ASC`. Crash-resilient, transaction-safe, portable across sqlite/postgres (rides E2's engine). **Note:** this is an architectural change from the in-mem `queue_manager`, not just a label. *Accept:* interleaved enqueue → high-lane items run ahead of low; queue order survives a simulated process restart.
- **e5.5 OpenTelemetry spans (optional)** — `PIXELPIVOT_OTEL_ENABLED=0` default, lazy import. Spans on quality-curve calc, staging file creation, backend exec. *Accept:* enabled → spans export to OTLP collector; disabled → zero overhead, module not imported.

---

**Total: 5 epics, 21 child beads.**

Each epic should be implemented on its own branch via the beads-tdd-python flow (RED-DEV-GREEN-REFACTOR), one PR per epic.
