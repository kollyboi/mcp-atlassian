"""Module for Jira permission scheme operations.

All endpoints are Jira Cloud REST API v3 and require the *Administer Jira*
global permission (OAuth scope ``manage:jira-configuration`` or an admin API
token). Write operations are gated by ``check_write_access`` in the server layer.

Every write operation supports ``dry_run``, which defaults to ``True``: the
method reports the current state and the payload that would be sent without
calling the API.

Response shapes below follow Atlassian's OpenAPI definition and were checked
against a live Jira Cloud site: the grants endpoint is *not* paginated and
returns ``{"permissions": [...]}``, while the issue security endpoints are
either paginated (``values``) or wrapped (``issueSecuritySchemes``).
"""

import logging
import re
from typing import Any
from urllib.parse import urlencode

from .client import JiraClient
from .screens import _require_id

logger = logging.getLogger("mcp-jira")

_PAGE_SIZE = 100
_PERMISSION_KEY_PATTERN = re.compile(r"^[A-Za-z0-9_.\-]+$")
_TASK_URL_PATTERN = re.compile(r"/rest/api/3/task/(\d+)/?$")
_MAX_TEXT_LENGTH = 255

# Holder types documented for
# POST /rest/api/3/permissionscheme/{schemeId}/permission, mapped to whether an
# identifier (``parameter`` or ``value``) is required.
_PERMISSION_HOLDER_TYPES: dict[str, bool] = {
    "anyone": False,
    "applicationRole": True,
    "assignee": False,
    "group": True,
    "groupCustomField": True,
    "projectLead": False,
    "projectRole": True,
    "reporter": False,
    "sd.customer.portal.only": False,
    "user": True,
    "userCustomField": True,
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


def _require_permission_key(value: Any) -> str:
    """Validate a permission key such as ``SET_ISSUE_SECURITY``.

    App-provided permissions contain dots, e.g.
    ``com.vendor.app__project-permission``.

    Raises:
        ValueError: If the value is empty or has unsafe characters.
    """
    text = str(value).strip() if value is not None else ""
    if not _PERMISSION_KEY_PATTERN.match(text):
        msg = f"Invalid permission: {value!r}"
        raise ValueError(msg)
    return text


def _require_text(name: str, value: Any) -> str:
    """Validate free text sent in a JSON body (group names, account IDs).

    Unlike ``_require_id`` this allows spaces, non-ASCII letters and ``:``,
    because the value is never interpolated into a URL.

    Raises:
        ValueError: If the value is empty, too long or has control characters.
    """
    text = str(value).strip() if value is not None else ""
    if not text or len(text) > _MAX_TEXT_LENGTH or any(ord(char) < 32 for char in text):
        msg = f"Invalid {name}: {value!r}"
        raise ValueError(msg)
    return text


def _holder_summary(holder: dict[str, Any] | None) -> dict[str, Any]:
    """Flatten a holder object, adding a readable name when it was expanded."""
    holder = holder or {}
    name = None
    for key, name_field in (
        ("projectRole", "name"),
        ("group", "name"),
        ("user", "displayName"),
        ("field", "name"),
        ("applicationRole", "name"),
    ):
        expanded = holder.get(key)
        if isinstance(expanded, dict):
            name = expanded.get(name_field)
            break
    return {
        "type": holder.get("type"),
        "parameter": holder.get("parameter"),
        "value": holder.get("value"),
        "name": name,
    }


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


class AdminHelpersMixin(JiraClient):
    """Shared request helpers for the permission and security mixins."""

    def _expect_dict(self, response: Any, endpoint: str) -> dict[str, Any]:
        if not isinstance(response, dict):
            msg = f"Unexpected response type from {endpoint}: {type(response)}"
            raise TypeError(msg)
        return response

    def _expect_key(self, response: Any, key: str, endpoint: str) -> list[Any]:
        """Return ``response[key]``, failing loudly if the shape is unexpected.

        Silently treating a missing key as an empty list would hide an API
        shape mismatch behind a plausible "no results" answer.
        """
        body = self._expect_dict(response, endpoint)
        value = body.get(key)
        if not isinstance(value, list):
            msg = (
                f"Unexpected response from {endpoint}: expected a list under "
                f"'{key}', got keys {sorted(body)}"
            )
            raise TypeError(msg)
        return value

    def _get_all_pages(
        self, path: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """GET every page of a ``startAt``/``maxResults`` paginated endpoint."""
        values: list[dict[str, Any]] = []
        start_at = 0
        while True:
            query = {**(params or {}), "startAt": start_at, "maxResults": _PAGE_SIZE}
            response = self.jira.get(f"{path}?{urlencode(query, doseq=True)}")
            page = self._expect_key(response, "values", path)
            values.extend(page)
            if response.get("isLast") is not False or not page:
                return values
            start_at += len(page)

    def _resolve_project_id(self, project_key: str) -> str:
        """Resolve a project key (or numeric ID) to its numeric project ID."""
        value = _require_id("project_key", project_key)
        if value.isdigit():
            return value
        endpoint = f"rest/api/3/project/{value}"
        project = self._expect_dict(self.jira.get(endpoint), endpoint)
        if not project.get("id"):
            msg = f"Project {project_key} has no id in the API response"
            raise TypeError(msg)
        return str(project["id"])


class PermissionsMixin(AdminHelpersMixin):
    """Mixin for Jira permission scheme operations."""

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
            Dict with ``project``, ``scheme_id``, ``name``, ``description`` and
            ``self``.
        """
        project = _require_id("project_key", project_key)
        endpoint = f"rest/api/3/project/{project}/permissionscheme"
        scheme = self._expect_dict(self.jira.get(endpoint), endpoint)
        if scheme.get("id") is None:
            msg = f"Unexpected response from {endpoint}: no scheme id"
            raise TypeError(msg)
        return {
            "project": project,
            "scheme_id": str(scheme["id"]),
            "name": scheme.get("name"),
            "description": scheme.get("description"),
            "self": scheme.get("self"),
        }

    def get_permission_scheme_grants(
        self, scheme_id: str, permission: str | None = None
    ) -> dict[str, Any]:
        """List the permission grants of a permission scheme.

        The endpoint has no server-side filter, so ``permission`` is applied
        after fetching the scheme's grants.

        Args:
            scheme_id: Numeric permission scheme ID.
            permission: Optional permission key to filter by, e.g.
                ``SET_ISSUE_SECURITY``.

        Returns:
            Dict with ``scheme_id``, ``permission``, ``total`` and ``grants``.
            Each grant has ``id``, ``permission`` and ``holder`` (``type``,
            ``parameter``, ``value`` and, when known, ``name``).
        """
        scheme = _require_numeric_id("scheme_id", scheme_id)
        key = _require_permission_key(permission) if permission else None
        endpoint = f"rest/api/3/permissionscheme/{scheme}/permission"
        response = self.jira.get(f"{endpoint}?{urlencode({'expand': 'all'})}")
        entries = self._expect_key(response, "permissions", endpoint)

        grants = [
            {
                "id": str(entry.get("id", "")),
                "permission": entry.get("permission"),
                "holder": _holder_summary(entry.get("holder")),
            }
            for entry in entries
            if key is None or entry.get("permission") == key
        ]
        return {
            "scheme_id": scheme,
            "permission": key,
            "total": len(grants),
            "grants": grants,
        }

    def get_async_task(self, task_id: str) -> dict[str, Any]:
        """Get the status of an asynchronous Jira REST API task.

        Args:
            task_id: The numeric task ID, or the ``location`` URL returned by
                an asynchronous operation.

        Returns:
            Dict with ``task_id``, ``status`` (``ENQUEUED``, ``RUNNING``,
            ``COMPLETE``, ``FAILED``, ``CANCEL_REQUESTED``, ``CANCELLED`` or
            ``DEAD``), ``progress`` (percent), ``message``, ``result`` and the
            ``submitted``/``started``/``finished`` timestamps.
        """
        value = str(task_id).strip()
        match = _TASK_URL_PATTERN.search(value)
        task = match.group(1) if match else _require_numeric_id("task_id", value)
        endpoint = f"rest/api/3/task/{task}"
        body = self._expect_dict(self.jira.get(endpoint), endpoint)
        return {
            "task_id": str(body.get("id") or task),
            "status": body.get("status"),
            "progress": body.get("progress"),
            "message": body.get("message"),
            "result": body.get("result"),
            "submitted": body.get("submitted"),
            "started": body.get("started"),
            "finished": body.get("finished"),
            "elapsed_runtime": body.get("elapsedRuntime"),
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
        holder_parameter: str | None = None,
        holder_value: str | None = None,
        *,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Grant a permission to a group, user, project role or similar holder.

        Args:
            scheme_id: Numeric permission scheme ID.
            permission: Permission key, e.g. ``SET_ISSUE_SECURITY``.
            holder_type: One of ``anyone``, ``applicationRole``, ``assignee``,
                ``group``, ``groupCustomField``, ``projectLead``,
                ``projectRole``, ``reporter``, ``sd.customer.portal.only``,
                ``user``, ``userCustomField``.
            holder_parameter: Group name, account ID, project role ID,
                application role key or custom field ID. Not used by
                ``anyone``, ``assignee``, ``projectLead`` and ``reporter``.
            holder_value: Group ID for ``group`` (Atlassian recommends it over
                the name, which can change); same as the parameter otherwise.
            dry_run: When ``True`` (the default), report what would change
                without calling the API.

        Returns:
            The dry-run preview, or the created grant when executed.
        """
        scheme = _require_numeric_id("scheme_id", scheme_id)
        key = _require_permission_key(permission)
        if holder_type not in _PERMISSION_HOLDER_TYPES:
            msg = (
                f"Invalid holder_type {holder_type!r}; expected one of "
                f"{', '.join(sorted(_PERMISSION_HOLDER_TYPES))}"
            )
            raise ValueError(msg)

        holder: dict[str, str] = {"type": holder_type}
        if holder_parameter:
            holder["parameter"] = _require_text("holder_parameter", holder_parameter)
        if holder_value:
            holder["value"] = _require_text("holder_value", holder_value)
        if _PERMISSION_HOLDER_TYPES[holder_type] and len(holder) == 1:
            msg = f"holder_type {holder_type!r} needs holder_parameter or holder_value"
            raise ValueError(msg)

        endpoint = f"rest/api/3/permissionscheme/{scheme}/permission"
        payload = {"holder": holder, "permission": key}

        if dry_run:
            existing = self.get_permission_scheme_grants(scheme, key)
            duplicate = [
                grant
                for grant in existing["grants"]
                if grant["holder"].get("type") == holder_type
                and all(
                    grant["holder"].get(field) == holder[field]
                    for field in ("parameter", "value")
                    if field in holder
                )
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
            "permission": response.get("permission"),
            "holder": _holder_summary(response.get("holder")),
        }

    def remove_permission_grant(
        self, scheme_id: str, grant_id: str, *, dry_run: bool = True
    ) -> dict[str, Any]:
        """Remove a permission grant from a permission scheme.

        Args:
            scheme_id: Numeric permission scheme ID.
            grant_id: Numeric ID of the grant to remove, from
                ``get_permission_scheme_grants``.
            dry_run: When ``True`` (the default), report what would change
                without calling the API.

        Returns:
            The dry-run preview, or the removal result when executed.
        """
        scheme = _require_numeric_id("scheme_id", scheme_id)
        grant = _require_numeric_id("grant_id", grant_id)
        endpoint = f"rest/api/3/permissionscheme/{scheme}/permission/{grant}"

        if dry_run:
            query = urlencode({"expand": "all"})
            found = self._expect_dict(self.jira.get(f"{endpoint}?{query}"), endpoint)
            current = {
                "id": str(found.get("id", grant)),
                "permission": found.get("permission"),
                "holder": _holder_summary(found.get("holder")),
            }
            return _preview("remove_permission_grant", endpoint, {}, current)

        self.jira.delete(endpoint)
        return {
            "success": True,
            "dry_run": False,
            "action": "remove_permission_grant",
            "scheme_id": scheme,
            "grant_id": grant,
        }
