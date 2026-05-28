# Task 004 — Reject empty / zero-selection matrices at the API boundary

**Severity:** MEDIUM (defense-in-depth; silent zero-work "success")
**Phase:** II — permutation / zero-selection
**Confidence:** Confirmed by runtime probe

## Problem

The Streamlit GUI blocks empty selections (`run_panel.py:55-56`), but the **backend does
not**. Probe:

```python
BatchRequest(source_dir='.', target_dir='.', target_format=[], tool=[], category=[])
# -> ACCEPTED: target_format=[] tool=[] category=[]
```

Consequences in `orchestrator.execute_batch`:
- `total_conversions = len(input_paths) * 0 = 0` (`orchestrator.py:104`)
- the matrix loop body never executes
- `input_bytes *= 0` -> `savings_pct = 0` (`orchestrator.py:170,192`)
- run is marked `completed` with `total_images=0`

So a direct API caller (or a mis-configured hot folder) gets a **silent no-op that
reports success**. Same gap for empty-after-resolve paths: `_resolve_path("")` returns the
CWD rather than erroring (see [task_007](task_007_path_containment_hardening.md)).

## Fix

Add Pydantic `min_length=1` constraints to the matrix lists on both request models
(`BatchRequest` and `HotFolderRequest`, `models.py:23-47`):

```python
from typing import Annotated
from pydantic import Field

class BatchRequest(BaseModel):
    source_dir: str
    target_dir: str
    target_format: Annotated[list[str], Field(min_length=1)]
    tool: Annotated[list[Tool], Field(min_length=1)]
    category: Annotated[list[str], Field(min_length=1)] = ["general"]
    trigger_type: str = "manual"
```

A 422 from FastAPI is the correct response; `api_client.py` already surfaces
`response.json()["detail"]` to the GUI.

## Acceptance criteria
- `POST /batch/start` with any of `target_format`/`tool`/`category` empty returns 422,
  not 200.
- Hot-folder registration with empty lists is likewise rejected.
- The GUI guard remains (fast feedback) but is now backed by server-side enforcement.
- Existing happy-path tests still pass.
