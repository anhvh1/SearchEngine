# Milestone Search Engine Implementation Plan

> **For agentic workers:** Use executing-plans to implement this plan task-by-task. Execute locally; do not deploy into running Milestone services without validating the target environment.

**Goal:** Build the runnable ingestion, profile, search, AI and Milestone integration project described in ARCHITECTURE-V2.

**Architecture:** A Python API owns a durable inbox, versioned profiles and permission-filtered search index. A C# MIP plugin forwards notifications using a disk outbox. A browser console provides search and administration; the Smart Client plugin opens the same console.

**Tech Stack:** Python 3.12, FastAPI/Pydantic, SQLite WAL/FTS5 for a single-node installation, HTML/CSS/JavaScript, C# .NET Framework 4.8 with installed MIP SDK. Optional local Ollama and faster-whisper adapters.

## Global constraints

- Never write to Milestone SQL tables or copy recording banks.
- Separate alarm/event identity; source timestamps must include timezone.
- Profiles are declarative, immutable versions; explicit validation and activation.
- Authenticated server-side source ACL before search, counts, evidence or AI.
- Unknown events remain searchable; profile conflicts and failures are visible.
- No implicit cloud AI processing, no browser credential persistence, no built-in credentials.
- Live vendor/metadata interfaces and Milestone identity need environment validation; unsupported paths must report unavailable.

## File structure and acceptance

### 1. Contracts, persistence and profile engine
Files: `backend/search_engine/{contracts,store,profiles}.py`, `tests/test_engine.py`, `pyproject.toml`.
Consumes JSON envelope; produces `Engine.ingest`, `process_pending`, `save_profile`, `validate_profile`, `activate_profile`, `search`.
- [ ] Write tests: repeated delivery yields one record; older updates cannot replace newer data; alarm/event remain distinct; conflicting profiles enter visible failure; unknown event remains searchable; replay preserves counts; ACL filters counts and records.
- [ ] Run `python -m pytest tests/test_engine.py -q`, observe missing behavior.
- [ ] Implement transactions, persistent pending work, bounded mapping, lifecycle and FTS.
- [ ] Repeat tests; verify restart with the same temporary database.

### 2. API, AI and connector services
Files: `backend/search_engine/{api,ai,connectors,cli}.py`, `tests/test_api.py`, `tests/test_connectors.py`.
Consumes engine; produces authenticated `/api` endpoints and command-line entry points.
- [ ] Test missing credentials, reader/admin separation, forged ACL rejection, JSON validation, grounded citations and failed connector checkpoint.
- [ ] Implement API, retry/replay/retention/audit, optional local model adapters and Milestone REST discovery/backfill.
- [ ] Run complete Python suite. Export OpenAPI contracts.

### 3. Operator and administrator tools
Files: `web/{index.html,app.js,style.css}`, `tests/ui.spec.js`.
Consumes API; produces search results, timeline, evidence references, profile editor/test/activate, discovery, operations.
- [ ] Write browser acceptance for login, search, result selection, profile editing and permission failures.
- [ ] Implement responsive Vietnamese console, explicit microphone recording and transcript review.
- [ ] Run browser acceptance against a temporary seeded database.

### 4. Milestone plugin
Files: `plugins/MilestoneSearch/*`, `plugins/MilestoneSearch.Tests/*`, `scripts/build-plugin.ps1`.
Consumes MIP notification; produces durable envelope deliveries. Smart Client exposes search entry point.
- [ ] Test spool retries/restart and event serialization using actual SDK-compatible types where available.
- [ ] Implement BackgroundPlugin subscription, bounded disk outbox, explicit settings and notification serialization.
- [ ] Build with installed VideoOS.Platform.dll; package without redistributing SDK assemblies.
- [ ] Document live-host acceptance separately from successful compilation.

### 5. Delivery and verification
Files: `README.md`, `docs/{DEPLOYMENT,ACCEPTANCE,SECURITY}.md`, `docs/decisions/0003-runtime.md`, scripts and example profiles.
- [ ] Supply installation/start/seed/build commands, isolated research import and configuration examples.
- [ ] Run Python and plugin tests, browser tests, build/package, secret and placeholder review.
- [ ] Record measured results and unverified live dependencies; do not call a lab build production-certified.
