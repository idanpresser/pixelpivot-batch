# PixelPivot Batch Engine TDD Implementation Plan

Based on the architectural blueprint and the strict rules of engagement provided, here is the complete sequence of Test-Driven Development (TDD) tasks to implement the High-Throughput Batch Engine.

---

# TASK: Workspace Initialization & Core Setup
**ID:** INIT1-001-WorkspaceSetup
**Phase:** Phase 1: Foundation
**Estimated Effort:** 1-2 Hours
**Dependencies:** None
**Target files:** `pixelpivot_batch/`, `pixelpivot_batch/.gitignore`, `pixelpivot_batch/app/core/__init__.py`, `tests/`

## 1. Objective
Establish an isolated workspace for the Batch Engine upgrade to protect the existing application. This task initializes a fresh Git repository in the `pixelpivot_batch` directory and copies the necessary foundational code from the main app, preparing the ground for the new microservice architecture.

## 2. Git Workflow & Branching
- **Epic Branch:** `feature/batch-engine-upgrade` (Create and ensure you are on this branch).
- **Task Branch:** `git checkout -b task/INIT1-001-WorkspaceSetup`
- **Commit Message:** `chore: initialize pixelpivot_batch workspace and copy base core modules`

## 3. Acceptance Criteria
- [ ] The `pixelpivot_batch` directory exists and is initialized as a fresh Git repository.
- [ ] Essential base code (`app/core/`, excluding GUI components) is copied into `pixelpivot_batch/app/core/`.
- [ ] A baseline `pytest` suite is configured and all copied legacy tests pass successfully (GREEN).
- [ ] No UI/GUI dependencies are imported in the copied core files.

## 4. Implementation Steps
1. **Directory Setup:** Create `pixelpivot_batch` and initialize it.
2. **Copy Core:** Copy database configuration, base DTOs, and utility modules to `pixelpivot_batch/app/core`.
3. **Purge GUI Dependencies:** Scrub the copied files of any Streamlit, PySide, or Qt imports to strictly enforce headless architecture.
4. **Test Harness Setup:** Set up `pytest`, `ruff`, and `mypy` configurations.

## 5. Testing & Validation
- **Static Analysis:** `ruff check pixelpivot_batch/`
- **Type Checking:** `mypy pixelpivot_batch/`
- **Unit Testing:** Run existing tests against the copied core modules to ensure baseline stability. RED -> GREEN.
- **Git Merge:** 
  ```bash
  git add .
  git commit -m "chore: initialize pixelpivot_batch workspace and copy base core modules"
  git checkout feature/batch-engine-upgrade
  git merge --no-ff task/INIT1-001-WorkspaceSetup
  ```

---

# TASK: Database Schema Upgrade (Batch Telemetry)
**ID:** DB1-002-BatchSchema
**Phase:** Phase 1: Foundation
**Estimated Effort:** 2 Hours
**Dependencies:** INIT1-001-WorkspaceSetup
**Target files:** `app/core/db/schema.py`, `app/core/db/repositories/batch.py`, `tests/test_db_batch.py`

## 1. Objective
Expand the existing database schema to include new tables optimized for high-throughput logging (`batch_runs` and `batch_summary`). This ensures batch telemetry is isolated from single-image analytics.

## 2. Git Workflow & Branching
- **Epic Branch:** `feature/batch-engine-upgrade`
- **Task Branch:** `git checkout -b task/DB1-002-BatchSchema`
- **Commit Message:** `feat(db): add batch_runs and batch_summary schema tables`

## 3. Acceptance Criteria
- [ ] `batch_runs` and `batch_summary` tables are successfully created upon DB initialization.
- [ ] CRUD operations for batch records are implemented in `app/core/db/repositories/batch.py`.
- [ ] SQLite/PostgreSQL foreign key constraints (`ON DELETE CASCADE`) are tested and enforced.

## 4. Implementation Steps
1. **Schema Definition:** Append the `CREATE TABLE` statements for `batch_runs` and `batch_summary` to `schema.py`.
2. **Repository Layer:** Create the `BatchRepository` class with `create_run`, `update_status`, and `save_summary` methods.
3. **Data Classes:** Create `Pydantic` models/DTOs representing the batch database rows.

## 5. Testing & Validation
- **Static Analysis:** `ruff check app/core/db/`
- **Type Checking:** `mypy app/core/db/`
- **Unit Testing:** Write `test_db_batch.py` to create a test DB, insert a batch run, link a summary, and verify cascade deletions. Doubt the existing connection lifecycle—ensure connections are properly closed. RED -> GREEN -> REFACTOR.
- **Git Merge:**
  ```bash
  git add .
  git commit -m "feat(db): add batch_runs and batch_summary schema tables"
  git checkout feature/batch-engine-upgrade
  git merge --no-ff task/DB1-002-BatchSchema
  ```

---

# TASK: Heuristic Interpolator Implementation
**ID:** CORE1-003-HeuristicInterpolator
**Phase:** Phase 1: Foundation
**Estimated Effort:** 2-3 Hours
**Dependencies:** INIT1-001-WorkspaceSetup
**Target files:** `app/core/heuristic_interpolator.py`, `tests/test_interpolator.py`

## 1. Objective
Implement the `HeuristicInterpolator` to calculate optimal quality settings for images that do not perfectly align with predefined resolution buckets, utilizing a mathematical average based on total Megapixels.

## 2. Git Workflow & Branching
- **Epic Branch:** `feature/batch-engine-upgrade`
- **Task Branch:** `git checkout -b task/CORE1-003-HeuristicInterpolator`
- **Commit Message:** `feat(core): implement HeuristicInterpolator for dynamic quality calculation`

## 3. Acceptance Criteria
- [ ] `HeuristicInterpolator` correctly maps input dimensions to exact Megapixels.
- [ ] It calculates a weighted average for qualities falling between the predefined buckets (Small, Medium, Large, XLarge).
- [ ] Edge cases (e.g., extremely small or massive images) safely clamp to minimum/maximum quality values without throwing exceptions.

## 4. Implementation Steps
1. **Class Skeleton:** Create `HeuristicInterpolator` and its initialization mapping logic.
2. **Mathematical Core:** Implement the linear interpolation algorithm calculating the distance between the two nearest buckets.
3. **Fallback Safety:** Implement min/max math to prevent quality values < 1 or > 100.

## 5. Testing & Validation
- **Static Analysis:** `ruff check app/core/heuristic_interpolator.py`
- **Type Checking:** `mypy app/core/heuristic_interpolator.py`
- **Unit Testing:** Write exact parameter combinations in `test_interpolator.py`. Ensure exact midpoints yield exact averages, and extremes clamp properly. Start with failing assertions. RED -> GREEN -> REFACTOR.
- **Git Merge:**
  ```bash
  git add .
  git commit -m "feat(core): implement HeuristicInterpolator for dynamic quality calculation"
  git checkout feature/batch-engine-upgrade
  git merge --no-ff task/CORE1-003-HeuristicInterpolator
  ```

---

# TASK: Converter Adapter Abstraction Refactor
**ID:** CONV2-001-BaseBatchAdapter
**Phase:** Phase 2: Converter Layer
**Estimated Effort:** 1-2 Hours
**Dependencies:** INIT1-001-WorkspaceSetup
**Target files:** `app/core/converters/base.py`, `tests/test_base_converter.py`

## 1. Objective
Refactor the `BaseConverter` abstract base class to support batch execution (`convert_batch()`) without breaking the existing single-image `convert()` interface, adhering to the Open/Closed Principle.

## 2. Git Workflow & Branching
- **Epic Branch:** `feature/batch-engine-upgrade`
- **Task Branch:** `git checkout -b task/CONV2-001-BaseBatchAdapter`
- **Commit Message:** `refactor(converters): add convert_batch interface to BaseConverter`

## 3. Acceptance Criteria
- [ ] `BaseConverter` includes an `@abstractmethod` for `convert_batch()`.
- [ ] Existing single-file converter implementations are stubbed/updated so they don't break during inheritance checks.
- [ ] A generic fallback batching method using thread pools is implemented as a default.

## 4. Implementation Steps
1. **Interface Update:** Add `convert_batch` abstract signature.
2. **Default Implementation:** Provide a baseline `default_batch_convert` that iterates `convert()` in a `ThreadPoolExecutor` for compatibility.
3. **Stub Downstream:** Add placeholder `convert_batch` methods to `MagickConverter`, `FFmpegConverter`, etc., returning `NotImplementedError` or calling the default.

## 5. Testing & Validation
- **Static Analysis:** `ruff check app/core/converters/`
- **Type Checking:** `mypy app/core/converters/`
- **Unit Testing:** Test that a dummy converter implementing only `convert()` falls back to the threaded batch executor. Test instantiation of existing converters to ensure ABC rules don't crash. RED -> GREEN.
- **Git Merge:**
  ```bash
  git add .
  git commit -m "refactor(converters): add convert_batch interface to BaseConverter"
  git checkout feature/batch-engine-upgrade
  git merge --no-ff task/CONV2-001-BaseBatchAdapter
  ```

---

# TASK: MagickConverter Native Batch Support
**ID:** CONV2-002-MagickBatch
**Phase:** Phase 2: Converter Layer
**Estimated Effort:** 3 Hours
**Dependencies:** CONV2-001-BaseBatchAdapter
**Target files:** `app/core/converters/magick.py`, `tests/test_magick_batch.py`

## 1. Objective
Implement the native `mogrify` batch processing inside the `MagickConverter` to leverage ImageMagick's optimized bulk engine instead of spawning individual subprocesses.

## 2. Git Workflow & Branching
- **Epic Branch:** `feature/batch-engine-upgrade`
- **Task Branch:** `git checkout -b task/CONV2-002-MagickBatch`
- **Commit Message:** `feat(converters): implement native mogrify batch execution for MagickConverter`

## 3. Acceptance Criteria
- [ ] `convert_batch` dynamically groups input images by identical output quality requirements.
- [ ] For each group, it correctly formats and executes a `mogrify -path <outdir> -format <fmt> -quality <q> <files>` command.
- [ ] Standard Out/Err streams are correctly parsed to populate `success_count` and `failure_count`.

## 4. Implementation Steps
1. **Grouping Logic:** Iterate through `qualities` and `input_paths`, creating a dictionary mapping quality -> List[files].
2. **Command Construction:** Build the `mogrify` subprocess arguments securely.
3. **Execution & Parsing:** Run the subprocess and parse the results, capturing the duration.

## 5. Testing & Validation
- **Static Analysis:** `ruff check app/core/converters/magick.py`
- **Type Checking:** `mypy app/core/converters/magick.py`
- **Unit Testing:** Write mock subprocess calls. Doubt the shell injection safety: explicitly test filenames with spaces, quotes, and malicious strings. Test the fallback mechanism if `mogrify` fails entirely. RED -> GREEN -> REFACTOR.
- **Git Merge:**
  ```bash
  git add .
  git commit -m "feat(converters): implement native mogrify batch execution for MagickConverter"
  git checkout feature/batch-engine-upgrade
  git merge --no-ff task/CONV2-002-MagickBatch
  ```

---

# TASK: FastAPI Orchestrator Service
**ID:** API3-001-HeadlessBatchAPI
**Phase:** Phase 3: Headless API
**Estimated Effort:** 4 Hours
**Dependencies:** CORE1-003-HeuristicInterpolator, CONV2-001-BaseBatchAdapter
**Target files:** `app/batch_api/main.py`, `app/batch_api/routes.py`, `app/batch_api/orchestrator.py`, `app/batch_api/models.py`, `tests/api/test_routes.py`

## 1. Objective
Create the headless FastAPI microservice that receives REST commands, leverages the `HeuristicInterpolator` to calculate qualities, triggers batch conversions, and logs telemetry to the database.

## 2. Git Workflow & Branching
- **Epic Branch:** `feature/batch-engine-upgrade`
- **Task Branch:** `git checkout -b task/API3-001-HeadlessBatchAPI`
- **Commit Message:** `feat(api): implement FastAPI REST application and orchestrator logic`

## 3. Acceptance Criteria
- [ ] `POST /api/v1/batch/start` accepts payload, creates DB row, and delegates to `BackgroundTasks`.
- [ ] `GET /api/v1/batch/status/{run_id}` retrieves the DB status correctly.
- [ ] The `BatchOrchestrator` integrates the Database, Interpolator, and Converters successfully.

## 4. Implementation Steps
1. **Pydantic Models:** Define `BatchRequest` and `BatchStatusResponse`.
2. **API Routes:** Wire the endpoints.
3. **Orchestrator Logic:** Implement `execute_batch` logic: scan dir -> interpolate qualities -> convert -> save to `batch_summary`.

## 5. Testing & Validation
- **Static Analysis:** `ruff check app/batch_api/`
- **Unit Testing:** Use `TestClient` to send mock requests. Mock the underlying converter to prevent real disk I/O. Verify the API returns immediately (HTTP 202/200) while background execution occurs. Verify DB rows are created.
- **Git Merge:**
  ```bash
  git add .
  git commit -m "feat(api): implement FastAPI REST application and orchestrator logic"
  git checkout feature/batch-engine-upgrade
  git merge --no-ff task/API3-001-HeadlessBatchAPI
  ```

---

# TASK: Hot Folder Watchdog Implementation
**ID:** API3-002-HotFolder
**Phase:** Phase 3: Headless API
**Estimated Effort:** 3 Hours
**Dependencies:** API3-001-HeadlessBatchAPI
**Target files:** `app/batch_api/hot_folder.py`, `tests/api/test_hot_folder.py`

## 1. Objective
Integrate the Python `watchdog` library to silently monitor designated directories for new images, automatically grouping them and triggering the batch orchestrator asynchronously.

## 2. Git Workflow & Branching
- **Epic Branch:** `feature/batch-engine-upgrade`
- **Task Branch:** `git checkout -b task/API3-002-HotFolder`
- **Commit Message:** `feat(api): implement hot folder watchdog event handler and debouncer`

## 3. Acceptance Criteria
- [ ] `HotFolderHandler` captures `FileCreatedEvent` inside target directories.
- [ ] A debouncing mechanism (e.g., 5 seconds of silence) groups multiple incoming files into a single batch run.
- [ ] Triggered batches correctly route to the `BatchOrchestrator`.

## 4. Implementation Steps
1. **Watchdog Setup:** Initialize `Observer` and custom `FileSystemEventHandler`.
2. **Debounce Logic:** Implement threading timer/asyncio sleep logic to wait for the final file write.
3. **Integration:** Dispatch the event as a synthetic API request or direct call to the Orchestrator.

## 5. Testing & Validation
- **Unit Testing:** Doubt the filesystem reliability. Simulate rapid, staggered file creations using `tempfile`. Assert that only *one* batch trigger is fired after the debounce window. RED -> GREEN -> REFACTOR.
- **Git Merge:**
  ```bash
  git add .
  git commit -m "feat(api): implement hot folder watchdog event handler and debouncer"
  git checkout feature/batch-engine-upgrade
  git merge --no-ff task/API3-002-HotFolder
  ```

---

# TASK: Streamlit GUI - Batch Layout & Run Panel
**ID:** GUI4-001-StreamlitApp
**Phase:** Phase 4: Standalone GUI
**Estimated Effort:** 3 Hours
**Dependencies:** API3-001-HeadlessBatchAPI
**Target files:** `app/web/batch_gui/main.py`, `app/web/batch_gui/api_client.py`, `app/web/batch_gui/panels/run_panel.py`

## 1. Objective
Build the decoupled, standalone Streamlit frontend. This task focuses on the API Client wrapper and the Manual Run tab, where users configure their target directories and trigger processing.

## 2. Git Workflow & Branching
- **Epic Branch:** `feature/batch-engine-upgrade`
- **Task Branch:** `git checkout -b task/GUI4-001-StreamlitApp`
- **Commit Message:** `feat(gui): initialize decoupled Streamlit ui and manual run panel`

## 3. Acceptance Criteria
- [ ] `api_client.py` provides resilient methods (`requests.post`, `.get`) with retry logic for connecting to the FastAPI backend.
- [ ] Streamlit interface features a tabbed layout, with the "MANUAL RUN" tab rendering input fields for paths, formats, and tools.
- [ ] Submitting the form posts to the REST API and dynamically switches to a polling progress state.

## 4. Implementation Steps
1. **API Client:** Use the `requests` library to interface with the batch API backend.
2. **Main Layout:** Create `st.tabs` in `main.py`.
3. **Run Panel:** Build the form using `st.text_input` and `st.selectbox`. Connect the submit button to `api_client.start_batch()`.

## 5. Testing & Validation
- **Unit Testing:** Write mock tests for `api_client.py` using `responses` or `requests_mock` to verify network error handling (connection refused, 500s). Doubt the robustness of the connection—what if the API is down? RED -> GREEN -> REFACTOR.
- **Git Merge:**
  ```bash
  git add .
  git commit -m "feat(gui): initialize decoupled Streamlit ui and manual run panel"
  git checkout feature/batch-engine-upgrade
  git merge --no-ff task/GUI4-001-StreamlitApp
  ```

---

# TASK: Docker Infrastructure Modifications
**ID:** INFRA5-001-DockerStack
**Phase:** Phase 5: Deployment
**Estimated Effort:** 1-2 Hours
**Dependencies:** GUI4-001-StreamlitApp
**Target files:** `docker-compose.yml`

## 1. Objective
Update the deployment stack to include the separated microservices, exposing the appropriate ports and ensuring the FastAPI and Streamlit containers communicate correctly over the internal Docker network.

## 2. Git Workflow & Branching
- **Epic Branch:** `feature/batch-engine-upgrade`
- **Task Branch:** `git checkout -b task/INFRA5-001-DockerStack`
- **Commit Message:** `chore(infra): add headless API and Streamlit GUI to docker-compose`

## 3. Acceptance Criteria
- [ ] `pixelpivot-batch-api` service added, mapping port 8000.
- [ ] `pixelpivot-batch-gui` service added, mapping port 8503, depending on the API service.
- [ ] Shared volume mounts are configured to allow both services to access `/workspace/dataset`.

## 4. Implementation Steps
1. **Docker Compose Edits:** Add the new service blocks as specified in the blueprint.
2. **Environment Variables:** Ensure `BATCH_API_URL` is passed correctly to the GUI container.

## 5. Testing & Validation
- **Integration Testing:** Run `docker-compose up --build`. Verify both services start successfully. Execute a `curl` against port 8000 to verify API life, and open port 8503 in a browser to verify GUI loading.
- **Git Merge:**
  ```bash
  git add .
  git commit -m "chore(infra): add headless API and Streamlit GUI to docker-compose"
  git checkout feature/batch-engine-upgrade
  git merge --no-ff task/INFRA5-001-DockerStack
  ```
