# HTTP Client Connection Reuse — Design

**Date:** 2026-06-23
**Branch context:** observed in API logs during live polling — each `GET /api/v1/batch/{id}/progress` arrives from a new client ephemeral port (e.g. `127.0.0.1:58388`, `58396`, `58397`).

## Problem

API access logs show a different client source port on every request to the same endpoint:

```
[API] INFO: 127.0.0.1:58388 - "GET /api/v1/batch/770/progress HTTP/1.1" 200 OK
[API] INFO: 127.0.0.1:58396 - "GET /api/v1/batch/770/progress HTTP/1.1" 200 OK
[API] INFO: 127.0.0.1:58397 - "GET /api/v1/batch/770/progress HTTP/1.1" 200 OK
```

The number after `127.0.0.1:` is the **client's ephemeral source port**, not the server port (server stays 8000). A new port per request means a new TCP connection per request — keep-alive / connection pooling is not happening.

Root cause: both HTTP clients construct a fresh `httpx.Client` per call, inside a `with` block that closes it immediately after.

- `app/web/batch_gui/api_client.py` — every method does `with httpx.Client() as client:`. New connection pool, new TCP connection, torn down at block end.
- `app/tui/api_client.py:20-21` — `_client()` returns a **new** `httpx.Client(transport=..., timeout=10.0)` on each `_get`/`_post`. The Telemetry screen polls `/progress` on a loop, so each poll opens and closes its own connection → fresh ephemeral port every time.

`httpx.Client` *is* the session abstraction (it owns the connection pool). Creating one per request defeats it — same per-call cost as bare `requests.get()`.

## Impact

Functional behaviour is correct (requests succeed). Cost is wasted overhead, worse under fast polling:
- Full TCP handshake + teardown on every poll.
- Closed connections linger in `TIME_WAIT` (~1–4 min each); a tight poll loop accumulates many sockets.
- Port churn through the ephemeral range.

## Fixes

### A. TUI client — reuse a single `httpx.Client` (long-lived process)

`app/tui/api_client.py`. The TUI is a long-running process, so the fix is direct: lazily create one `httpx.Client` and reuse it across calls.

- Add `self._client_instance: Optional[httpx.Client] = None` in `__init__`.
- `_client()` creates the instance on first use (honouring `self._transport` for tests) and returns the cached instance thereafter.
- `_get`/`_post` use the shared client directly (no `with` block — must not close it per call).
- Add a `close()` method to release the pool on TUI shutdown; wire it into the app teardown path.
- Keep `_transport` injectable for tests; constructing the client lazily preserves the existing test seam.

### B. GUI client — reuse via `st.session_state` (Streamlit rerun model)

`app/web/batch_gui/api_client.py`. Streamlit re-executes the script top-to-bottom on every interaction, so a plain instance attribute is recreated each rerun and buys nothing. The client (or the `APIClient`) must be cached across reruns.

- Hold a single `httpx.Client` as an instance attribute, created in `__init__`; replace each `with httpx.Client() as client:` with `self._client.<verb>(...)`.
- Cache the `APIClient` itself in `st.session_state` (e.g. `st.session_state.setdefault("api_client", APIClient(base_url))`) at the call sites in the GUI, so the same instance — and its pool — survives reruns.
- Confirm where `APIClient` is currently instantiated in `app/web/batch_gui/` and route all uses through the cached instance.

## Out of scope
- Poll cadence / client-side throttling (separate concern; see prior batch-network-robustness spec, item C).
- Switching to async clients.
- HTTP/2 or explicit connection-pool sizing/tuning.

## Testing
- **A:** unit test — calling two TUI client methods in sequence reuses one `httpx.Client` instance (assert identity of the cached client across calls). Existing `_transport` injection still works (mock transport observes both requests). `close()` releases the instance. Avoid icons / non-ASCII in test strings.
- **B:** unit test — `APIClient` issues multiple requests over a single underlying client (mock transport, assert one client instance). Streamlit `session_state` caching verified at the call site (instance identity stable across simulated reruns, or a thin unit around the `setdefault` helper).

## Beads
- **A** — TUI api_client connection reuse (own bead)
- **B** — GUI api_client connection reuse + Streamlit session_state caching (own bead)

Independent; either order. B carries the extra Streamlit-rerun nuance, so it is the larger of the two.
