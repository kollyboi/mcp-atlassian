# Implement Jira Read Tools

Status: resolved

## Description
Implement five new dedicated read tools across Jira mixins and register them (along with `jira_get_project_notification_scheme`) in `src/mcp_atlassian/servers/jira.py`.

## Acceptance Criteria
- [x] All six tools are strictly read-only (`readOnlyHint: True`, tags `{"jira", "read"}`).
- [x] Explicit error separation: 403 Forbidden ("ikke tillatt for denne brukeren") vs 404 Not Found ("finnes ikke").
- [x] `IssuesMixin.get_issue_properties`: listing without key, JSON value with length limit + `truncated` flag with key.
- [x] `ProjectsMixin.get_project_properties`: listing without key, JSON value with length limit + `truncated` flag with key.
- [x] `FieldsMixin.get_field_screens`: resolves field name (handles multi-match like "Fag" cleanly), includes screen ID, name, tab name, and project usages.
- [x] `PermissionsMixin.check_permissions`: resolves project/issue keys to IDs, supports `account_id` evaluation via `POST /permissions/check`, explicit 403 handling.
- [x] `UsersMixin.get_group_members`: supports group name or group ID, paginated, explicit 403 handling.
- [x] Server tools registered in `src/mcp_atlassian/servers/jira.py`.
- [x] Unit tests covering all methods and error cases (403, 404, multi-field match, truncation).
- [x] Live integration tests against SST: `SST-223` for issue properties, `SST` for project properties, `customfield_10186` and `customfield_10199` for field screens.
- [x] `uv run pytest tests/unit/` and `pre-commit run --all-files` pass cleanly.

## Answer
Implemented all six dedicated read tools across `IssuesMixin`, `ProjectsMixin`, `FieldsMixin`, `PermissionsMixin`, `UsersMixin` and registered them in `src/mcp_atlassian/servers/jira.py`.
Verified live against SST:
- `SST-223`: returned `pbrr-assessment` (risk model 6656e2f4a1b48917c6eb885e, inherent: R3/C/Middels, residual: R3/B/Liten)
- `SST`: returned `pbrr-settings` (issueTypeId: 10062)
- "Fag": disambiguated both `customfield_10186` (on screen 10288 / create) and `customfield_10199` (on screen 10292 / view and edit)
- Espen Viddal (`esvi@cowi.com`): evaluated as having `TRANSITION_ISSUES` on SST
- "SST - Alle tilganger": listed 13 members
- SST notification scheme: resolved "Default Notification Scheme"
All unit tests and pre-commit checks pass cleanly.
