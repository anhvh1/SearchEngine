# Complete Milestone Components Implementation Plan

> **For agentic workers:** Execute inline using executing-plans; test every boundary before implementing it.

**Goal:** Supply actual Event Server, Management Client and Smart Client plugins plus a PostgreSQL-backed service.

**Architecture:** Reuse lifecycle and configuration extension patterns from `docs/PsimEvent` and `docs/PsimManagement`, with independent IDs and no modifications to those reference projects. PostgreSQL stores the durable inbox, profiles, context, audit and searchable records; MIP plugins call the authenticated backend.

**Tech Stack:** .NET Framework 4.8, installed Milestone 25.2 SDK, WinForms/WPF host integrations, Python/FastAPI, psycopg/PostgreSQL 18.

## Constraints
- Never install over PSIM plugins or modify Milestone databases.
- Store credentials only in ignored local configuration or OS secret storage; no secrets in shared Milestone configuration.
- Event callbacks do no network or AI work. Durable outbox forwards data asynchronously and reports failures.
- Backend roles and source ACL enforce access independently of the plugin UI.
- Preserve SDK event type/message IDs and raw metadata; distinguish configured types from observed messages.

## Tasks
- [ ] `plugins/MilestoneSearch/{Outbox,EnvelopeMapper,CollectorPlugin}.cs`: run existing console harness red; implement atomic spool files and retry; map actual Alarm/BaseEvent SDK types; build and run harness. Subscribe singular NewEvent only, alarm new/change and configuration changes; changed-alarm hints enqueue REST reconciliation, not invented alarm content.
- [ ] `plugins/MilestoneSearch/{PluginDefinition,PluginSettings,Management,SmartClient}.cs`: adapt ItemNode/ItemManager from supplied samples. Admin form provides connection, catalog, profile load/save/test/activate/replay. Smart Client workspace embeds native search/results/evidence controls and links playback through MIP messages after resolving camera identity. Compile with installed SDK and package with `plugin.def`.
- [ ] `backend/search_engine/database.py`, `tests/test_postgres.py`: run real PostgreSQL tests for idempotence/restart/ACL/profile lifecycle/retention; implement explicit dialect boundary and native GIN full-text indexing. Create separate application and test databases; do not touch existing data.
- [ ] `backend/search_engine/{api,connectors,store}.py`: add persistent reconciliation/discovery/context, sample retrieval and status APIs needed by plugin; integration tests must cover collector and admin separation.
- [ ] `scripts/*`, `README.md`, `docs/{DEPLOYMENT,ACCEPTANCE}.md`: reproducible install/build/run; local credentials ignored; report build/test/live-host evidence separately. Existing reference projects remain unchanged.

## Verification commands
```powershell
python -m pytest -q
dotnet build plugins/MilestoneSearch/MilestoneSearch.csproj -c Release
dotnet run --project plugins/MilestoneSearch.Tests/MilestoneSearch.Tests.csproj
python tests/browser_acceptance.py
```
