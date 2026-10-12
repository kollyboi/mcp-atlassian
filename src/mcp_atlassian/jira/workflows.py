"""Jira workflow administration operations."""

from typing import Any

from .client import JiraClient
from .screens import _require_id


class WorkflowsMixin(JiraClient):
    """Mixin for Jira Cloud workflow administration."""

    def search_workflows(
        self,
        query: str | None = None,
        start_at: int = 0,
        max_results: int = 50,
        expand: str | None = None,
    ) -> dict[str, Any]:
        """Search workflow definitions.

        The query is a substring match against the workflow name. The legacy
        ``/workflow/search`` endpoint only matched exact names (its
        ``workflowName`` parameter), so partial names returned nothing.

        Transitions and their rules are only returned when ``expand`` is set
        (e.g. ``values.transitions``); without it Jira omits them from the
        response, which makes the result unsafe to feed back into
        ``update_workflows``.
        """
        params: dict[str, Any] = {
            "startAt": start_at,
            "maxResults": min(max_results, 50),
        }
        if query:
            params["queryString"] = query
        if expand:
            params["expand"] = expand
        response = self.jira.get("rest/api/3/workflows/search", params=params)
        if not isinstance(response, dict):
            raise TypeError(
                f"Unexpected response type from workflow search: {type(response)}"
            )
        return response

    def update_workflows(
        self,
        workflows: list[dict[str, Any]],
        statuses: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any] | list[Any]:
        """Update workflow definitions in Jira's workflow administration API.

        Jira's ``WorkflowUpdateRequest`` has ``statuses`` next to ``workflows``
        at the top level: the statuses to associate with the workflows
        (``name``, ``statusCategory``, ``statusReference``, plus ``id`` when
        reusing an existing status). Jira has been seen rejecting an update
        that omits it.
        """
        if not workflows:
            raise ValueError("workflows must contain at least one workflow")
        payload: dict[str, Any] = {"workflows": workflows}
        if statuses:
            payload["statuses"] = statuses
        response = self.jira.post("rest/api/3/workflows/update", json=payload)
        if not isinstance(response, dict | list):
            raise TypeError(
                f"Unexpected response type from workflow update: {type(response)}"
            )
        return response

    def validate_update_workflows(
        self,
        workflows: list[dict[str, Any]],
        statuses: list[dict[str, Any]] | None = None,
        levels: list[str] | None = None,
    ) -> dict[str, Any]:
        """Validate a workflow update payload without applying it.

        Takes the same ``workflows`` and ``statuses`` as ``update_workflows``
        and returns Jira's ``{"errors": [...]}`` list. ``levels`` limits the
        result to ``WARNING`` and/or ``ERROR``.
        """
        if not workflows:
            raise ValueError("workflows must contain at least one workflow")
        payload: dict[str, Any] = {"workflows": workflows}
        if statuses:
            payload["statuses"] = statuses
        body: dict[str, Any] = {"payload": payload}
        if levels:
            body["validationOptions"] = {"levels": levels}
        response = self.jira.post("rest/api/3/workflows/update/validation", json=body)
        if not isinstance(response, dict):
            raise TypeError(
                f"Unexpected response type from workflow validation: {type(response)}"
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
