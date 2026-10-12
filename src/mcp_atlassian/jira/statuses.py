"""Jira status administration operations."""

from typing import Any

from .client import JiraClient
from .screens import _require_id


class StatusesMixin(JiraClient):
    """Mixin for Jira Cloud status administration."""

    def search_statuses(
        self,
        query: str | None = None,
        project_id: str | None = None,
        issue_type_id: str | None = None,
        start_at: int = 0,
        max_results: int = 50,
    ) -> dict[str, Any]:
        """Search statuses, optionally scoped to a project or issue type."""
        params: dict[str, Any] = {
            "startAt": start_at,
            "maxResults": min(max_results, 50),
        }
        if query:
            params["query"] = query
        if project_id:
            params["projectId"] = _require_id("project_id", project_id)
        if issue_type_id:
            params["issueTypeId"] = _require_id("issue_type_id", issue_type_id)
        response = self.jira.get("rest/api/3/statuses/search", params=params)
        if not isinstance(response, dict):
            raise TypeError(
                f"Unexpected response type from status search: {type(response)}"
            )
        return response

    def create_statuses(
        self,
        statuses: list[dict[str, Any]],
        scope: dict[str, Any] | None = None,
    ) -> list[dict[str, Any]]:
        """Create statuses.

        Jira requires ``scope`` at the top level of the payload, next to
        ``statuses``; it is rejected on the individual statuses. Defaults to
        ``{"type": "GLOBAL"}``. Use ``{"type": "PROJECT", "project": {"id": ...}}``
        for a project-scoped status.
        """
        if not statuses:
            raise ValueError("statuses must contain at least one status")
        response = self.jira.post(
            "rest/api/3/statuses",
            json={"scope": scope or {"type": "GLOBAL"}, "statuses": statuses},
        )
        if not isinstance(response, list):
            raise TypeError(
                f"Unexpected response type from status creation: {type(response)}"
            )
        return response

    def update_statuses(self, statuses: list[dict[str, Any]]) -> list[dict[str, Any]]:
        """Update existing statuses."""
        if not statuses:
            raise ValueError("statuses must contain at least one status")
        response = self.jira.put("rest/api/3/statuses", data={"statuses": statuses})
        if not isinstance(response, list):
            raise TypeError(
                f"Unexpected response type from status update: {type(response)}"
            )
        return response

    def get_status_usages(self, status_id: str) -> dict[str, Any] | list[Any]:
        """Get workflows that use a status."""
        status = _require_id("status_id", status_id)
        response = self.jira.get(f"rest/api/3/statuses/{status}/workflowUsages")
        if not isinstance(response, dict | list):
            raise TypeError(
                f"Unexpected response type from status usages: {type(response)}"
            )
        return response
