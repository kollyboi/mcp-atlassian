# Dedicated Domain Tools Over Generic REST Endpoint

Jira MCP exposes dedicated, strongly-typed tools (`jira_get_issue_properties`, `jira_get_project_properties`, `jira_get_field_screens`, `jira_check_permissions`, `jira_get_group_members`) rather than a generic pass-through read tool (`jira_rest_get`).

## Context

When expanding read capabilities across Jira Cloud APIs, a generic `jira_rest_get(path, query)` was considered to cover numerous read endpoints at once. However, critical Jira read operations like effective permission checks (`/rest/api/3/permissions/check`) and bulk fetches (`/rest/api/3/issue/bulkfetch`) are HTTP `POST` endpoints in the Atlassian REST API, rendering a GET-only tool incapable of checking user permissions. Furthermore, Jira Cloud endpoints frequently require numeric entity IDs instead of alphanumeric keys (e.g. `10052` instead of `SST`), mandatory query parameters, and inconsistent pagination models.

## Decision

We implement discrete, high-level domain tools with explicit FastMCP schemas and automatic key-to-ID resolution. Each tool handles normalization, transparent resolution of project and issue keys to Atlassian numeric IDs, and returns compact JSON payloads optimized for LLM context windows.

## Considered Options

- **Generic `jira_rest_get(path, query)`**: Rejected because it cannot call POST-based query endpoints like `POST /permissions/check`, forces LLMs to guess opaque URL paths and query parameter requirements, and exposes unvalidated URL inputs.
- **Dedicated domain tools**: Accepted. Provides strict type-safety, clear LLM discovery, transparent ID resolution, and aligns with existing MCP server conventions.
