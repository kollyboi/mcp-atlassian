"""Module for Jira permission scheme operations.

All endpoints are Jira Cloud REST API v3 and require the *Administer Jira*
global permission (OAuth scope ``manage:jira-configuration`` or an admin API
token). Write operations are gated by ``check_write_access`` in the server layer.

Every write operation supports ``dry_run``, which defaults to ``True``: the
method reports the current state and the payload that would be sent without
calling the API.
"""

import logging
from typing import Any
from urllib.parse import urlencode

from .client import JiraClient
from .screens import _require_id

logger = logging.getLogger("mcp-jira")

_PAGE_SIZE = 100

# Permission holder types accepted by
# POST /rest/api/3/permissionscheme/{schemeId}/permission
_PERMISSION_HOLDER_TYPES = {
    "group",
    "user",
    "projectRole",
    "applicationRole",
    "internalUser",
}


def _require_numeric_id(name: str, value: Any) -> str:
    """Validate an identifier that must be numeric.

    Args:
        name: Parameter name, used in the error message.
        value: The value to validate.

    Returns:
        The identifier as a string.

    Raises:
        ValueError: If the value is not a positive integer.
    """
    text = str(value).strip() if value is not None else ""
    if not text.isdigit():
        msg = f"{name} must be numeric, got: {value!r}"
        raise ValueError(msg)
    return text


def _preview(tool: str, endpoint: str, payload: dict[str, Any], current: Any) -> dict:
    """Build the result of a dry-run write operation."""
    return {
        "success": True,
        "dry_run": True,
        "action": tool,
        "endpoint": endpoint,
        "current": current,
        "would_apply": payload,
    }


class PermissionsMixin(JiraClient):
    """Mixin for Jira permission scheme operations."""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _expect_dict(self, response: Any, endpoint: str) -> dict[str, Any]:
        if not isinstance(response, dict):
            msg = f"Unexpected response type from {endpoint}: {type(response)}"
            raise TypeError(msg)
        return response

    def _expect_list(self, response: Any, endpoint: str) -> list[dict[str, Any]]:
        if not isinstance(response, list):
            msg = f"Unexpected response type from {endpoint}: {type(response)}"
            raise TypeError(msg)
        return response

    def _get_all_pages(
        self, path: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """GET every page of a ``startAt``/``maxResults`` paginated endpoint."""
        values: list[dict[str, Any]] = []
        start_at = 0
        while True:
            query = {**(params or {}), "startAt": start_at, "maxResults": _PAGE_SIZE}
            response = self.jira.get(f"{path}?{urlencode(query, doseq=True)}")
            if not isinstance(response, dict):
                msg = f"Unexpected response type from {path}: {type(response)}"
                raise TypeError(msg)
            page = response.get("values", [])
            values.extend(page)
            if response.get("isLast") is not False or not page:
                return values
            start_at += len(page)

    def _resolve_project_id(self, project_key: str) -> str:
        """Resolve a project key (or numeric ID) to its numeric project ID."""
        value = _require_id("project_key", project_key)
        if value.isdigit():
            return value
        project = self._expect_dict(
            self.jira.get(f"rest/api/3/project/{value}"), "project"
        )
        if not project.get("id"):
            msg = f"Project {project_key} has no id in the API response"
            raise TypeError(msg)
        return str(project["id"])

    def _resolve_permission_name(self, permission: str) -> str:
        """Resolve a permission key such as ``SET_ISSUE_SECURITY`` to its ID.

        Jira accepts either the numeric permission ID or the permission key in
        a grant payload, so an unknown key is returned unchanged.
        """
        value = _require_id("permission", permission)
        if value.isdigit():
            return value
        known = self._get_all_pages("rest/api/3/permission")
        for entry in known:
            if entry.get("key") == value:
                return str(entry["id"])
        return value

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get_permission_scheme_for_project(self, project_key: str) -> dict[str, Any]:
        """Get the permission scheme assigned to a project.

        ``ProjectsMixin.get_project_permission_scheme`` returns the raw API
        payload and swallows errors; this version normalizes the result and
        raises so that the server tool can report the failure.

        Args:
            project_key: The project key or numeric project ID.

        Returns:
            Dict with ``project_id``, ``project_key``, ``scheme_id``, ``name``,
            ``description`` and ``self``.
        """
        project = _require_id("project_key", project_key)
        endpoint = f"rest/api/3/project/{project}/permissionscheme"
        scheme = self._expect_dict(self.jira.get(endpoint), endpoint)
        return {
            "project_id": str(scheme.get("projectId") or project),
            "project_key": scheme.get("key") or project,
            "scheme_id": str(scheme.get("id", "")),
            "name": scheme.get("name"),
            "description": scheme.get("description"),
            "self": scheme.get("self"),
        }

    def get_permission_scheme_grants(
        self, scheme_id: str, permission: str | None = None
    ) -> dict[str, Any]:
        """List the permission grants of a permission scheme.

        Args:
            scheme_id: Numeric permission scheme ID.
            permission: Optional permission key or ID to filter by, e.g.
                ``SET_ISSUE_SECURITY``.

        Returns:
            Dict with ``scheme_id``, ``total`` and ``grants``. Each grant has
            ``id``, ``permission_key``, ``permission_name`` and ``holder``.
        """
        scheme = _require_numeric_id("scheme_id", scheme_id)
        endpoint = f"rest/api/3/permissionscheme/{scheme}/permission"
        params: dict[str, Any] = {"expand": "all"}
        if permission:
            params["permission"] = _require_id("permission", permission)

        values = self._get_all_pages(endpoint, params=params)
        grants = [
            {
                "id": str(entry.get("id", "")),
                "permission_key": entry.get("key"),
                "permission_name": entry.get("name"),
                "holder": {
                    "type": (entry.get("holder") or {}).get("type"),
                    "parameter": (entry.get("holder") or {}).get("parameter"),
                    "value": (entry.get("holder") or {}).get("value"),
                },
            }
            for entry in values
        ]
        return {
            "scheme_id": scheme,
            "permission": permission,
            "total": len(grants),
            "grants": grants,
        }

    def get_async_task(self, task_id: str) -> dict[str, Any]:
        """Get the status of an asynchronous Jira REST API task.

        Args:
            task_id: The task ID, or the full ``self`` URL returned by an
                asynchronous operation.

        Returns:
            Dict with ``task_id``, ``executedBy``, ``startedAt``, ``finishedAt``,
            ``result`` and ``location``.
        """
        value = str(task_id).strip()
        if value.startswith("http"):
            endpoint = value.split("/rest/api/3/", 1)[-1]
        else:
            value = _require_id("task_id", value)
            endpoint = f"rest/api/3/task/{value}"
        task = self._expect_dict(self.jira.get(endpoint), endpoint)
        return {
            "task_id": str(task.get("id") or value),
            "executedBy": task.get("executedBy"),
            "startedAt": task.get("startedAt"),
            "finishedAt": task.get("finishedAt"),
            "result": task.get("result"),
            "location": task.get("location"),
        }

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def set_project_permission_scheme(
        self, project_key: str, scheme_id: str, *, dry_run: bool = True
    ) -> dict[str, Any]:
        """Assign a permission scheme to a project.

        Args:
            project_key: The project key or numeric project ID.
            scheme_id: Numeric permission scheme ID.
            dry_run: When ``True`` (the default), report what would change
                without calling the API.

        Returns:
            The dry-run preview, or the assigned scheme when executed.
        """
        scheme = _require_numeric_id("scheme_id", scheme_id)
        project_id = self._resolve_project_id(project_key)
        endpoint = f"rest/api/3/project/{project_id}/permissionscheme"
        payload = {"id": int(scheme)}

        if dry_run:
            current = self.get_permission_scheme_for_project(project_id)
            return _preview("set_project_permission_scheme", endpoint, payload, current)

        response = self._expect_dict(self.jira.put(endpoint, data=payload), endpoint)
        return {
            "success": True,
            "dry_run": False,
            "action": "set_project_permission_scheme",
            "project_id": project_id,
            "scheme_id": str(response.get("id", scheme)),
            "name": response.get("name"),
        }

    def set_permission_grant(
        self,
        scheme_id: str,
        permission: str,
        holder_type: str,
        holder_parameter: str,
        *,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Grant a permission to a group, user, project role or application role.

        Args:
            scheme_id: Numeric permission scheme ID.
            permission: Permission key or ID, e.g. ``SET_ISSUE_SECURITY``.
            holder_type: One of ``group``, ``user``, ``projectRole``,
                ``applicationRole``, ``internalUser``.
            holder_parameter: Group name, account ID, role ID or application
                role ID.
            dry_run: When ``True`` (the default), report what would change
                without calling the API.

        Returns:
            The dry-run preview, or the created grant when executed.
        """
        scheme = _require_numeric_id("scheme_id", scheme_id)
        if holder_type not in _PERMISSION_HOLDER_TYPES:
            msg = (
                f"Invalid holder_type {holder_type!r}; expected one of "
                f"{', '.join(sorted(_PERMISSION_HOLDER_TYPES))}"
            )
            raise ValueError(msg)
        parameter = _require_id("holder_parameter", holder_parameter)
        permission_id = self._resolve_permission_name(permission)

        endpoint = f"rest/api/3/permissionscheme/{scheme}/permission"
        payload = {
            "holder": {"type": holder_type, "parameter": parameter},
            "permission": permission_id,
        }

        if dry_run:
            existing = self.get_permission_scheme_grants(scheme_id, permission)
            duplicate = [
                grant
                for grant in existing["grants"]
                if grant["holder"].get("type") == holder_type
                and grant["holder"].get("parameter") == parameter
            ]
            preview = _preview("set_permission_grant", endpoint, payload, existing)
            preview["duplicate"] = bool(duplicate)
            preview["existing_grant_ids"] = [grant["id"] for grant in duplicate]
            return preview

        response = self._expect_dict(self.jira.post(endpoint, data=payload), endpoint)
        return {
            "success": True,
            "dry_run": False,
            "action": "set_permission_grant",
            "scheme_id": scheme,
            "grant_id": str(response.get("id", "")),
            "permission_key": response.get("key"),
            "holder": response.get("holder"),
        }

    def remove_permission_grant(
        self, scheme_id: str, grant_id: str, *, dry_run: bool = True
    ) -> dict[str, Any]:
        """Remove a permission grant from a permission scheme.

        Args:
            scheme_id: Numeric permission scheme ID.
            grant_id: Numeric ID of the grant to remove.
            dry_run: When ``True`` (the default), report what would change
                without calling the API.

        Returns:
            The dry-run preview, or the removal result when executed.
        """
        scheme = _require_numeric_id("scheme_id", scheme_id)
        grant = _require_numeric_id("grant_id", grant_id)
        endpoint = f"rest/api/3/permissionscheme/{scheme}/permission/{grant}"

        if dry_run:
            current = self._expect_dict(self.jira.get(endpoint), endpoint)
            return _preview("remove_permission_grant", endpoint, {}, current)

        self.jira.delete(endpoint)
        return {
            "success": True,
            "dry_run": False,
            "action": "remove_permission_grant",
            "scheme_id": scheme,
            "grant_id": grant,
        }
