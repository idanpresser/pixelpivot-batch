# Production Roadmap: PixelPivot Batch Engine

This document outlines the architectural plan to transition the PixelPivot Batch Engine from its current development state to a production-ready, high-availability image processing system.

## Phase 1: Database & Data Portability [COMPLETE]
**Goal:** Ensure the heuristic system and repository layers are platform-agnostic (PostgreSQL/SQLite) and resilient to type fluctuations.

- [x] **Dual-Engine Repository Abstraction:**
    - Refactored `app/core/db/repositories/batch.py` using `Dialect` abstraction.
    - Handled `%s` (Postgres) vs `?` (SQLite) parameter markers.
- [x] **Heuristic Table Generation Tool:**
    - Created `tools/generate_heuristic_data.py`.
    - Supports SQLite and PostgreSQL with robust type-casting.
- [x] **Database Migration Layer:**
    - Updated `schema.py` to be dialect-aware with SQLite optimizations (WAL).

## Phase 2: GUI Polish & Styling [COMPLETE]
**Goal:** Align the Batch Engine GUI with the aesthetic standards of the main PixelPivot suite.

- [x] **Unified Theme Configuration:**
    - Implemented `theme_engine.py` using Cyberpunk palette (Cyan on Deep Charcoal).
- [x] **Custom CSS Injection:**
    - Injected Google Fonts (Space Grotesk, JetBrains Mono, Inter).
    - Modern card-based layouts and Pulse animations for status.
- [x] **Interactive Telemetry Visualizations:**
    - Refactored `main.py` and `run_panel.py` for branding and modern UX.

## Phase 3: High-Performance Converter Optimizations [IN PROGRESS]
**Goal:** Eliminate per-batch bottlenecks and ensure converter stability.

- [x] **Sharp Daemon Lifecycle Management:**
    - Finalized `SharpConverter` with persistent Node.js daemon and Socket Pipelining.
- [x] **Circuit Breaker for Converters:**
    - Enhanced `BaseConverter` with failure threshold and fatal error detection.
- [ ] **Pre-flight Resource Validation:**
    - Add a check to `BatchOrchestrator` to verify available Disk Space and RAM before starting 10,000+ image batches.

## Phase 4: Production Deployment & Containerization [COMPLETE]
**Goal:** A "Single Command" deployment that is secure and scalable.

- [x] **Multi-Stage Docker Build:**
    - Optimized `Dockerfile` bundling Python 3.12 and Node.js 20.
- [x] **Health Checks & Auto-Restart:**
    - Added `HEALTHCHECK` and `docker-compose` health-aware dependencies.
- [x] **Environment Hardening:**
    - Integrated WSL paths (`/mnt/i/...`) and created `scripts/wsl_start.sh`.
- [x] **CLI Access:**
    - Added `pixelpivot-cli` service for interactive terminal access.

## Phase 5: Verification & Stress Testing
**Goal:** Prove the system can handle production loads.

- [ ] **Large-Scale Integration Test:**
    - Create a test script that fires 5 concurrent batches of 1,000 images each.
    - Assert no database deadlocks and that the GUI remains responsive.
- [ ] **Cross-DB Validation Test:**
    - Automated test runner that executes the full suite against a temporary SQLite file AND a Dockerized Postgres instance.
