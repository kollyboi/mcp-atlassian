# Spec: Project Role Actors Management

## Context
Jira MCP has tools for managing Permission Schemes, but no tools for managing Project Roles and Role Actors. In Jira, permissions are granted to Project Roles, and users/groups are assigned to those roles as Role Actors per project.

## Requirements
Implement four tools in Jira MCP:
1. `get_project_roles(project_key: str)`
2. `get_project_role_actors(project_key: str, role_id_or_name: str)`
3. `add_project_role_actors(project_key: str, role_id_or_name: str, users: str | None = None, groups: str | None = None, dry_run: bool = True)`
4. `remove_project_role_actors(project_key: str, role_id_or_name: str, users: str | None = None, groups: str | None = None, dry_run: bool = True)`

## Behavior
- Use `GET /rest/api/3/project/{project_key}/roledetails` to fetch structured role details.
- Support both numeric `role_id` and case-insensitive role names in `role_id_or_name`. If not found, error message lists valid roles and IDs.
- Accept single values or comma-separated lists for `users` (account IDs) and `groups` (group names or group IDs).
- Write operations use `@check_write_access` and default to `dry_run=True`.
- `add_project_role_actors` is idempotent: filters out existing members and reports `already_members`.
- `remove_project_role_actors` loops over actors sequentially against `DELETE /rest/api/3/project/{project_key}/role/{role_id}` and reports all removed actors.
