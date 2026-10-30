# Spec: Jira Read Tools

## Context
Analysis of Jira REST API gaps revealed missing read tools in Jira MCP needed to investigate app configurations, effective permissions, field screen usages, and group memberships.

## Guarantees
- **Strictly read-only**: All six tools are strictly read-only and have zero side effects. No state or configuration is mutated in Jira.
- **Explicit 403 vs 404 error differentiation**:
  - `403 Forbidden`: Reported as "Ikke tillatt for denne brukeren: handlingen krever administratorrettigheter som det gjeldende API-tokenet mangler."
  - `404 Not Found`: Reported as "Ikke funnet: ressursen finnes ikke i Jira."

## Tool Specifications

1. `jira_get_issue_properties(issue_id_or_key: str, property_key: str | None = None, max_length: int = 15000)`
   - Without `property_key`: calls `GET /rest/api/3/issue/{key}/properties` and returns the list of property key names (`{"keys": ["checklist", "pbrr-assessment", ...]}`).
   - With `property_key`: calls `GET /rest/api/3/issue/{key}/properties/{propertyKey}` and returns `{"key": str, "value": Any, "truncated": bool}`. If JSON string length exceeds `max_length`, truncates cleanly and sets `truncated: true`.

2. `jira_get_project_properties(project_key_or_id: str, property_key: str | None = None, max_length: int = 15000)`
   - Same behavior as issue properties, against `GET /rest/api/3/project/{key}/properties`.

3. `jira_get_field_screens(field_id_or_name: str, project_keys: list[str] | str | None = None)`
   - Disambiguation: if given a name like `"Fag"` matching multiple fields (e.g. `customfield_10186` and `customfield_10199`), returns screen results for all matching fields with clear field ID labeling so no match is silently ignored.
   - For each field: resolves screens from `GET /rest/api/3/field/{fieldId}/screens`, identifies the specific tab where the field is placed via `GET /rest/api/3/screens/{screenId}/tabs/{tabId}/fields`, and determines project usage.
   - Returns structured list: `[{"field_id": "...", "field_name": "...", "screens": [{"id": 10288, "name": "...", "tab_id": 10303, "tab_name": "...", "projects": [...]}]}]`.

4. `jira_check_permissions(permissions: list[str] | str, project_key_or_id: str | None = None, issue_key_or_id: str | None = None, account_id: str | None = None)`
   - Evaluates effective permissions via `POST /rest/api/3/permissions/check`.
   - Supports checking for another user via `account_id` (or email/username resolved via `UsersMixin`).
   - If user lacks permission to check another user (403), returns an explicit "ikke tillatt for denne brukeren" response instead of a misleading "mangler rettighet".
   - Automatically resolves project key to numeric project ID and issue key to numeric issue ID.

5. `jira_get_group_members(group_name_or_id: str, max_results: int = 50, start_at: int = 0, include_inactive: bool = False)`
   - Accepts group name or UUID `groupId`.
   - Calls `GET /rest/api/3/group/member`.
   - On 403, returns explicit "ikke tillatt for denne brukeren (krever administratorrettigheter)".
   - Returns normalized list: `{"group": str, "total": int, "members": [{"account_id": "...", "display_name": "...", "email_address": "...", "active": bool}], "is_last": bool}`.

6. `jira_get_project_notification_scheme(project_key: str)`
   - Exposes existing `ProjectsMixin.get_project_notification_scheme` as FastMCP read tool.
