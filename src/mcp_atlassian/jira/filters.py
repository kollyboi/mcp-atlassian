"""Jira filter operations (saved JQL searches).

Filters require the *Manage Jira* permission to create and update; write
operations are gated by ``check_write_access`` in the server layer.
"""

import logging
from typing import Any

from requests.exceptions import HTTPError

from .client import JiraClient
from .screens import _require_id

logger = logging.getLogger("mcp-jira")


class FiltersMixin(JiraClient):
    """Mixin for Jira Cloud filter operations."""

    def search_filters(
        self,
        filter_name: str | None = None,
        *,
        substring_match: bool = False,
        owner: str | None = None,
        group_name: str | None = None,
        project_id: str | None = None,
        order_by: str | None = None,
        start_at: int = 0,
        max_results: int = 50,
    ) -> dict[str, Any]:
        """Search filters by name.

        Args:
            filter_name: Filter name to search for. Without
                ``substring_match`` this only matches names starting with
                the given text; with it, names containing the text match.
            substring_match: Match anywhere in the filter name.
            owner: Owner user (username or account ID).
            group_name: Group whose filters are shared with the caller.
            project_id: Project the filters are shared with.
            order_by: Sort field, e.g. ``name`` or ``-name``.
            start_at: Zero-based result offset.
            max_results: Maximum results.

        Returns:
            Jira's paginated filter search response with ``values``,
            ``total``, ``isLast``, ``nextPage``.
        """
        params: dict[str, Any] = {"startAt": start_at, "maxResults": max_results}
        if filter_name:
            params["filterName"] = filter_name
            if substring_match:
                params["isSubstringMatch"] = "true"
        if owner:
            params["owner"] = owner
        if group_name:
            params["groupname"] = group_name
        if project_id:
            params["projectId"] = _require_id("project_id", project_id)
        if order_by:
            params["orderBy"] = order_by
        response = self.jira.get("rest/api/3/filter/search", params=params)
        if not isinstance(response, dict):
            msg = f"Unexpected response type from filter search: {type(response)}"
            raise TypeError(msg)
        return response

    def get_filter(self, filter_id: str) -> dict[str, Any]:
        """Get a filter by ID, including its JQL and share permissions.

        Args:
            filter_id: Numeric filter ID.

        Returns:
            The filter definition.
        """
        fid = _require_id("filter_id", filter_id)
        endpoint = f"rest/api/3/filter/{fid}"
        response = self.jira.get(endpoint)
        if not isinstance(response, dict):
            msg = f"Unexpected response type from {endpoint}: {type(response)}"
            raise TypeError(msg)
        return response

    def get_filter_columns(self, filter_id: str) -> list[dict[str, Any]]:
        """Get the issue navigator columns configured for a filter.

        Args:
            filter_id: Numeric filter ID.

        Returns:
            List of column dicts, e.g. ``[{"label": "Key", "value": "issuekey"}]``.
            Empty when the filter has no stored column layout.
        """
        fid = _require_id("filter_id", filter_id)
        endpoint = f"rest/api/3/filter/{fid}/columns"
        try:
            response = self.jira.get(endpoint)
        except HTTPError as e:
            # Filters that never had their column layout opened return 404.
            status = e.response.status_code if e.response is not None else None
            if status == 404:
                return []
            raise
        if not isinstance(response, list):
            msg = f"Unexpected response type from {endpoint}: {type(response)}"
            raise TypeError(msg)
        return response

    def create_filter(
        self,
        name: str,
        jql: str,
        description: str | None = None,
        favourite: bool | None = None,
    ) -> dict[str, Any]:
        """Create a filter owned by the current user.

        Args:
            name: Filter name; must be unique among the user's filters.
            jql: The JQL query for the filter.
            description: Optional filter description.
            favourite: Whether to select the filter as a favorite.

        Returns:
            The created filter, including its numeric ``id``.
        """
        payload: dict[str, Any] = {"name": name, "jql": jql}
        if description is not None:
            payload["description"] = description
        if favourite is not None:
            payload["favourite"] = favourite
        logger.info(f"Creating filter '{name}'")
        response = self.jira.post("rest/api/3/filter", json=payload)
        if not isinstance(response, dict):
            msg = f"Unexpected response type from filter create: {type(response)}"
            raise TypeError(msg)
        return response

    def update_filter(
        self,
        filter_id: str,
        name: str,
        jql: str,
        description: str | None = None,
        favourite: bool | None = None,
    ) -> dict[str, Any]:
        """Update a filter.

        This is a full replacement: the filter's name and JQL must both be
        supplied even when only one of them changes.

        Args:
            filter_id: Numeric filter ID.
            name: The filter's new name.
            jql: The filter's new JQL query.
            description: Optional new description.
            favourite: Whether the filter is selected as a favorite.

        Returns:
            The updated filter.
        """
        fid = _require_id("filter_id", filter_id)
        payload: dict[str, Any] = {"name": name, "jql": jql}
        if description is not None:
            payload["description"] = description
        if favourite is not None:
            payload["favourite"] = favourite
        logger.info(f"Updating filter {fid}")
        response = self.jira.put(f"rest/api/3/filter/{fid}", data=payload)
        if not isinstance(response, dict):
            msg = f"Unexpected response type from filter update: {type(response)}"
            raise TypeError(msg)
        return response

    def set_filter_columns(self, filter_id: str, columns: list[str]) -> None:
        """Set the issue navigator columns for a filter.

        This replaces the whole column list, so pass every column that
        should be visible.

        Args:
            filter_id: Numeric filter ID.
            columns: Column value names, e.g. ``["issuetype", "issuekey",
                "summary", "status", "assignee", "updated"]``. Free-form
                custom field names are allowed too, e.g. ``"Type Avvik"``.
        """
        if not columns:
            raise ValueError("columns must contain at least one column")
        fid = _require_id("filter_id", filter_id)
        logger.info(f"Setting columns for filter {fid}")
        self.jira.put(
            f"rest/api/3/filter/{fid}/columns", data={"columns": columns}
        )

    def add_filter_share_permission(
        self,
        filter_id: str,
        permission_type: str,
        *,
        account_id: str | None = None,
        group_name: str | None = None,
        group_id: str | None = None,
        project_id: str | None = None,
        project_role_id: str | None = None,
        rights: int | None = None,
    ) -> dict[str, Any]:
        """Add a share permission to a filter.

        Args:
            filter_id: Numeric filter ID.
            permission_type: One of ``user``, ``group``, ``project``,
                ``projectRole``, ``global``, ``authenticated``. Note that
                ``global`` and ``authenticated`` override all existing
                permissions.
            account_id: Account ID, for ``user``.
            group_name: Group name, for ``group``.
            group_id: Group ID, for ``group`` (more stable than the name).
            project_id: Project ID, for ``project``/``projectRole``.
            project_role_id: Project role ID, for ``projectRole``.
            rights: Permission bitmask (1 = view, 2 = edit).

        Returns:
            The created share permission.
        """
        fid = _require_id("filter_id", filter_id)
        if permission_type not in (
            "user",
            "group",
            "project",
            "projectRole",
            "global",
            "authenticated",
        ):
            raise ValueError(f"Invalid permission type: {permission_type!r}")
        payload: dict[str, Any] = {"type": permission_type}
        if account_id is not None:
            payload["accountId"] = account_id
        if group_name is not None:
            payload["groupname"] = group_name
        if group_id is not None:
            payload["groupId"] = group_id
        if project_id is not None:
            payload["projectId"] = _require_id("project_id", project_id)
        if project_role_id is not None:
            payload["projectRoleId"] = _require_id(
                "project_role_id", project_role_id
            )
        if rights is not None:
            payload["rights"] = rights
        logger.info(f"Adding {permission_type} share permission to filter {fid}")
        response = self.jira.post(
            f"rest/api/3/filter/{fid}/permission", json=payload
        )
        if not isinstance(response, dict):
            msg = f"Unexpected response type from share permission: {type(response)}"
            raise TypeError(msg)
        return response
