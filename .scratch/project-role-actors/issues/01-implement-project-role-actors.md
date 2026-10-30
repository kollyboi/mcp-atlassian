# Implement Project Role Actors Tools

Status: resolved

## Description
Implement project role actor tools in `ProjectsMixin` and register them in FastMCP server (`servers/jira.py`).

## Acceptance Criteria
- [x] `ProjectsMixin` methods:
  - `get_project_roles(project_key)` (updated to return structured role details while keeping compatibility or providing normalized output)
  - `get_project_role_actors(project_key, role_id_or_name)`
  - `add_project_role_actors(project_key, role_id_or_name, users=None, groups=None, dry_run=True)`
  - `remove_project_role_actors(project_key, role_id_or_name, users=None, groups=None, dry_run=True)`
- [x] FastMCP tools registered in `src/mcp_atlassian/servers/jira.py` with `@jira_mcp.tool`, tags, and `@check_write_access` on write tools.
- [x] Unit tests covering read, write, dry-run, role resolution, and edge cases.
- [x] `uv run pytest` and `pre-commit run --all-files` pass cleanly.

## Answer
Implemented in `src/mcp_atlassian/jira/projects.py` and `src/mcp_atlassian/servers/jira.py`, with unit tests in `tests/unit/jira/test_projects.py` and `tests/unit/servers/test_jira_server.py`. All tests and pre-commit checks pass.
