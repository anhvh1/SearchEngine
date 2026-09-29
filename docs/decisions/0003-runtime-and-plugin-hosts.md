# ADR 0003: PostgreSQL backend and three MIP host integrations

## Status
Accepted for the development build; production acceptance remains separate.

## Context
The user supplied working PSIM reference projects for Event Server and Management Client and a local PostgreSQL installation. The previous single-node prototype used SQLite and did not yet contain the actual MIP host components.

## Decision
Use PostgreSQL for deployed backend storage with pgvector exact cosine retrieval and a GIN full-text index. Keep SQLite for fast portable tests and optional demonstration only. Exact vector retrieval currently avoids approximate-search recall surprises under source filters; add dimension-specific ANN indexes only after measuring the target corpus.

Three .NET Framework 4.8 plugin assemblies have separate Plugin Definition IDs and packages: Event Server Service BackgroundPlugin, Administration ItemNode/ItemManager, and Smart Client WPF workspace. A machine installs only the package for its host. All three use the Management plugin's configuration owner ID so configuration remains shared through Milestone. Management and Smart Client reuse the WebView2 console source. The native bridge exposes camera playback only, checks the configured web origin, resolves cameras in the current Milestone session, and leaves video authorization to Milestone.

Milestone configuration stores URL, site and enablement. Service credentials remain outside shared Milestone configuration. Backend user tokens/source grants are explicit interim application identities, not claimed to be Milestone SSO. Event changes that carry only an alarm ID become persistent REST reconciliation jobs.

## Consequences
PostgreSQL and pgvector must be provisioned; client machines require WebView2 runtime. Plugin compilation against installed 25.2 libraries is evidence of API compatibility only, not proof of loading, permission behavior or playback in a running host. The supplied reference projects remain unchanged. See [acceptance](../ACCEPTANCE.md) for the outstanding live-system checks.
