# Recommendation: Migration to Distributed Task Orchestration

## Executive Summary
The current architecture of the PixelPivot Batch Engine is highly optimized for single-node performance, utilizing adaptive threading and local resource monitoring. However, as the system moves toward "extremely high-volume" processing requirements, the local `BackgroundTasks` model becomes a bottleneck. To scale horizontally across multiple compute nodes and ensure high availability, we recommend migrating to a distributed task queue architecture using **Celery** with **Redis** or **RabbitMQ**.

---

## 1. Architectural Shift: Local vs. Distributed

| Feature | Current (Single-Node) | Proposed (Distributed) |
| :--- | :--- | :--- |
| **Orchestration** | FastAPI `BackgroundTasks` | Celery Workers |
| **Task Storage** | In-Memory (Active) / SQLite (History) | Redis/RabbitMQ (Broker) |
| **Scaling** | Vertical (Bigger CPU/RAM) | Horizontal (More Worker Nodes) |
| **Persistence** | Local `pixelpivot.db` | Centralized PostgreSQL |
| **Concurrency** | Python `ThreadPoolExecutor` | Distributed Worker Processes |

---

## 2. Impact Analysis: Files, Classes, and Functions

### A. `app/batch_api/routes.py`
**Target:** Task Dispatching
- **Current:** Uses `fastapi.BackgroundTasks` to trigger `orchestrator.execute_batch`.
- **Refactor:** Replace `bg_tasks.add_task(...)` with `tasks.process_batch.delay(...)`.
- **Rationale:** Decouples the API response from the execution engine completely.

### B. `app/batch_api/orchestrator.py`
**Target:** `BatchOrchestrator` Class
- **Refactor:** This class should be split. 
    - **Metadata Logic:** Stays in the API layer to validate paths and scan files.
    - **Execution Logic:** The `execute_batch` method should be moved to a standalone Celery task.
- **Removed Functions:** `execute_batch` (as a local method).
- **New Functions:** `prepare_batch` (prepares DB records and returns task signature).

### C. `app/core/converters/base.py`
**Target:** `BaseConverter` and `_default_batch_convert`
- **Refactor:** While the `convert()` method remains the same, `convert_batch()` could be refactored to allow **Sub-Task Distribution**.
- **Proposal:** Instead of one large batch task, each individual image conversion can be a separate Celery task. This allows a 1,000-image batch to be spread across 10 different servers simultaneously.

### D. `app/core/db/` (The Persistence Layer)
**Target:** SQLite to PostgreSQL Transition
- **Files to Modify:** `app/core/db/connection.py`, `app/core/db/schema.py`.
- **Rationale:** SQLite (even in WAL mode) is not suitable for a distributed system where multiple nodes need write access to a central state. PostgreSQL is required for multi-node lock management and state consistency.

---

## 3. New Component: `app/tasks.py`
A new file must be created to define the Celery application and task logic.

```python
# New app/tasks.py structure
from celery import Celery
from .core.converters.ffmpeg_converter import FFmpegConverter

celery_app = Celery('pixelpivot', broker='redis://localhost:6379/0')

@celery_app.task(bind=True, max_retries=3)
def process_single_image(self, input_path, output_path, format, quality):
    # Isolated conversion logic
    converter = FFmpegConverter()
    return converter.convert(input_path, output_path, format, quality)

@celery_app.task
def process_batch_orchestration(run_id, request_data):
    # Logic moved from orchestrator.py
    # Triggers a 'Group' of process_single_image tasks
    pass
```

---

## 4. Implementation Steps

1. **Infrastructure Setup:** Deploy a Redis instance (Broker) and a PostgreSQL instance (Result Backend & State).
2. **Dependency Update:** Add `celery[redis]` and `psycopg2-binary` to `pyproject.toml`.
3. **Task Extraction:** Move the logic inside `BatchOrchestrator.execute_batch` into a Celery task in `app/tasks.py`.
4. **API Integration:** Update `app/batch_api/routes.py` to call `.delay()` on the new task.
5. **Worker Deployment:** Update `docker-compose.yml` to spin up "Worker" containers that run `celery -A app.tasks worker`.
6. **Monitoring:** Integrate **Flower** for real-time monitoring of distributed tasks.

---

## 5. Benefits and Trade-offs

### Pros:
- **Infinite Scalability:** Add worker nodes dynamically as volume increases.
- **Fault Tolerance:** If a worker node crashes, the task broker automatically re-assigns the job to another node.
- **Priority Queues:** Assign "HotFolder" tasks to high-priority queues and "Manual Batch" tasks to low-priority queues.

### Cons:
- **Increased Complexity:** Requires managing a broker (Redis) and a distributed database.
- **Network Overhead:** Images must be accessible via a shared file system (NAS/S3/NFS) so all nodes can read the source files.
- **Deployment Weight:** The air-gap bundle size will increase due to more infrastructure components.

---

## Conclusion
The PixelPivot Batch Engine is currently a "Ferrari in a garage"—extremely powerful but limited by the walls of a single machine. Transitioning to a distributed model will turn it into a scalable processing factory, capable of handling millions of images across a cluster of servers with minimal changes to the core conversion logic.
