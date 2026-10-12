"""Jira workflow administration operations."""

from typing import Any

from .client import JiraClient
from .screens import _require_id


class WorkflowsMixin(JiraClient):
    """Mixin for Jira Cloud workflow administration."""

    def search_workflows(
        self, query: str | None = None, start_at: int = 0, max_results: int = 50
    ) -> dict[str, Any]:
        """Search workflow definitions."""
        params: dict[str, Any] = {
            "startAt": start_at,
            "maxResults": min(max_results, 50),
        }
        if query:
            params["workflowName"] = query
        response = self.jira.get("rest/api/3/workflow/search", params=params)
        if not isinstance(response, dict):
            raise TypeError(
                f"Unexpected response type from workflow search: {type(response)}"
            )
        return response

    def update_workflows(
        self, workflows: list[dict[str, Any]]
    ) -> dict[str, Any] | list[Any]:
        """Update workflow definitions in Jira's workflow administration API."""
        if not workflows:
            raise ValueError("workflows must contain at least one workflow")
        response = self.jira.post(
            "rest/api/3/workflows/update", json={"workflows": workflows}
        )
        if not isinstance(response, dict | list):
            raise TypeError(
                f"Unexpected response type from workflow update: {type(response)}"
            )
        return response

    def publish_workflow_scheme(
        self,
        workflow_scheme_id: str,
        status_mappings: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | list[Any] | None:
        """Publish a draft workflow scheme, optionally with status mappings."""
        payload = {"statusMappings": status_mappings} if status_mappings else None
        scheme = _require_id("workflow_scheme_id", workflow_scheme_id)
        endpoint = f"rest/api/3/workflowscheme/{scheme}/draft/publish"
        response = (
            self.jira.post(endpoint, json=payload)
            if payload
            else self.jira.post(endpoint)
        )
        if response is not None and not isinstance(response, dict | list):
            raise TypeError(
                f"Unexpected response type from workflow publish: {type(response)}"
            )
        return response
