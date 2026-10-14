"""Module for Jira issue security scheme operations.

All endpoints are Jira Cloud REST API v3 and require the *Administer Jira*
global permission (OAuth scope ``manage:jira-configuration`` or an admin API
token). Write operations are gated by ``check_write_access`` in the server layer.

Every write operation supports ``dry_run``, which defaults to ``True``: the
method reports the current state and the payload that would be sent without
calling the API.
"""

import logging
from typing import Any
from urllib.parse import urlencode, urlsplit

from requests.exceptions import HTTPError

from ..exceptions import MCPAtlassianAuthenticationError
from .client import JiraClient
from .permissions import _preview, _require_id, _require_numeric_id

logger = logging.getLogger("mcp-jira")

_PAGE_SIZE = 100

# Member types accepted by PUT /rest/api/3/issuesecurityschemes/{schemeId}/level/{levelId}/member
_SECURITY_MEMBER_TYPES = {
    "reporter",
    "group",
    "user",
    "projectrole",
    "applicationRole",
    "allUsers",
}


class SecurityMixin(JiraClient):
    """Mixin for Jira issue security scheme operations."""

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _expect_dict(self, response: Any, endpoint: str) -> dict[str, Any]:
        if not isinstance(response, dict):
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

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def get_issue_security_schemes(self) -> list[dict[str, Any]]:
        """List all issue security schemes.

        Returns:
            List of schemes with ``id``, ``name``, ``description`` and
            ``default_level_id``.
        """
        endpoint = "rest/api/3/issuesecurityschemes"
        values = self._get_all_pages(endpoint)
        return [
            {
                "id": str(entry.get("id", "")),
                "name": entry.get("name"),
                "description": entry.get("description"),
                "default_level_id": str(entry.get("defaultLevelId") or ""),
            }
            for entry in values
        ]

    def get_issue_security_scheme_for_project(self, project_key: str) -> dict[str, Any]:
        """Find the issue security scheme associated with a project.

        Args:
            project_key: The project key or numeric project ID.

        Returns:
            Dict with ``project_id``, ``project_key``, ``scheme_id``, ``name``,
            ``default_level_id`` and ``levels``. A project without a security
            scheme returns ``scheme_id: None``.
        """
        project_id = self._resolve_project_id(project_key)
        endpoint = "rest/api/3/issuesecurityschemes/project"
        rows = self._get_all_pages(endpoint, params={"projectId": [project_id]})
        if not rows:
            return {
                "project_id": project_id,
                "project_key": project_key,
                "scheme_id": None,
                "name": None,
                "default_level_id": None,
                "levels": [],
            }

        row = rows[0]
        scheme_id = str(row.get("issueSecuritySchemeId") or "")
        levels = self.get_issue_security_levels(scheme_id) if scheme_id else []
        return {
            "project_id": project_id,
            "project_key": project_key,
            "scheme_id": scheme_id or None,
            "name": row.get("issueSecuritySchemeName"),
            "default_level_id": str(row.get("defaultSecurityLevelId") or "") or None,
            "levels": levels,
        }

    def get_issue_security_levels(self, scheme_id: str) -> list[dict[str, Any]]:
        """List the security levels of an issue security scheme.

        Args:
            scheme_id: Numeric issue security scheme ID.

        Returns:
            List of levels with ``id``, ``name``, ``description`` and
            ``project_count``. These IDs can be passed to
            ``get_security_level_members``.
        """
        scheme = _require_numeric_id("scheme_id", scheme_id)
        endpoint = "rest/api/3/issuesecurityschemes/level"
        values = self._get_all_pages(endpoint, params={"schemeId": [scheme]})
        return [
            {
                "id": str(entry.get("id", "")),
                "name": entry.get("name"),
                "description": entry.get("description"),
                "project_count": entry.get("projectCount"),
            }
            for entry in values
        ]

    def get_security_level_members(self, level_id: str | int) -> dict[str, Any]:
        """
        Get members (groups, users, roles) that have access to a security level.

        Uses the Jira Cloud REST API v3 endpoint:
            GET /rest/api/3/issuesecurityschemes/level/member?levelId={levelId}

        Args:
            level_id: The numeric ID of the security level.

        Returns:
            Dict with ``success``, ``level_id``, ``total``, and ``members`` keys.
            Each member has ``member_id``, ``type``, ``parameter``, and
            optionally ``value``. ``member_id`` is what
            ``remove_issue_security_level_member`` needs.

        Raises:
            MCPAtlassianAuthenticationError: On 401/403 responses.
        """
        try:
            response = self.jira.get(
                "rest/api/3/issuesecurityschemes/level/member",
                params={"levelId": str(level_id), "maxResults": "100"},
            )
            if not isinstance(response, dict):
                raise TypeError(
                    f"Unexpected response type from security level members API: {type(response)}"
                )

            values = response.get("values", [])
            members = []
            for entry in values:
                holder = entry.get("holder", {})
                member: dict[str, Any] = {
                    "member_id": str(entry.get("id", "")),
                    "type": holder.get("type"),
                    "parameter": holder.get("parameter"),
                }
                if holder.get("value"):
                    member["value"] = holder["value"]
                members.append(member)

            return {
                "success": True,
                "level_id": str(level_id),
                "total": response.get("total", len(members)),
                "members": members,
            }

        except HTTPError as http_err:
            if http_err.response is not None and http_err.response.status_code in (
                401,
                403,
            ):
                raise MCPAtlassianAuthenticationError(
                    f"Authentication/permission error fetching security level members: {http_err}"
                ) from http_err
            raise
        except Exception as e:
            logger.error(
                f"Error fetching security level members for level {level_id}: {e}"
            )
            raise

    # ------------------------------------------------------------------
    # Write operations
    # ------------------------------------------------------------------

    def set_project_issue_security_scheme(
        self,
        project_key: str,
        scheme_id: str | None,
        level_mappings: list[dict[str, str]] | None = None,
        *,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Associate an issue security scheme with a project.

        Passing ``None`` as ``scheme_id`` clears the association.
        ``level_mappings`` remaps existing issue levels to the new scheme, as
        ``[{"oldLevelId": ..., "newLevelId": ...}]``; unmapped issues lose their
        level. This operation is asynchronous, so executing it returns the task
        location rather than an immediate result.

        Args:
            project_key: The project key or numeric project ID.
            scheme_id: Numeric issue security scheme ID, or ``None`` to clear.
            level_mappings: Optional old-to-new level ID mappings.
            dry_run: When ``True`` (the default), report what would change
                without calling the API.

        Returns:
            The dry-run preview, or ``task_id`` and ``location`` when executed.
        """
        project_id = self._resolve_project_id(project_key)
        scheme = _require_numeric_id("scheme_id", scheme_id) if scheme_id else None

        mappings: list[dict[str, str]] = []
        for mapping in level_mappings or []:
            if not isinstance(mapping, dict):
                msg = f"Invalid level mapping: {mapping!r}"
                raise ValueError(msg)
            old = mapping.get("oldLevelId")
            new = mapping.get("newLevelId")
            mappings.append(
                {
                    "oldLevelId": _require_numeric_id("oldLevelId", old),
                    "newLevelId": _require_numeric_id("newLevelId", new),
                }
            )

        endpoint = "rest/api/3/issuesecurityschemes/project"
        payload: dict[str, Any] = {"projectId": project_id, "schemeId": scheme}
        if mappings:
            payload["oldToNewSecurityLevelMappings"] = mappings

        if dry_run:
            current = self.get_issue_security_scheme_for_project(project_id)
            unmapped = sorted(
                {
                    level["id"]
                    for level in current.get("levels", [])
                    if level["id"] not in {m["oldLevelId"] for m in mappings}
                }
            )
            preview = _preview(
                "set_project_issue_security_scheme", endpoint, payload, current
            )
            preview["unmapped_levels"] = unmapped
            preview["note"] = (
                "Executing this operation is asynchronous; the response returns "
                "a task location to poll."
            )
            return preview

        # The API answers 303 with a task location. Redirects must not be
        # followed (that would turn the PUT into a GET), and errors must raise.
        response = self.jira._session.put(
            self.jira.url_joiner(self.jira.url, endpoint),
            json=payload,
            headers=self.jira.default_headers,
            allow_redirects=False,
            timeout=self.jira.timeout,
        )
        if response.status_code >= 400:
            response.raise_for_status()
        location = response.headers.get("location") or ""
        task_id = ""
        if location:
            task_id = urlsplit(location).path.rsplit("/", 1)[-1]
        return {
            "success": True,
            "dry_run": False,
            "action": "set_project_issue_security_scheme",
            "project_id": project_id,
            "scheme_id": scheme,
            "task_id": task_id,
            "location": location,
        }

    def set_issue_security_level_member(
        self,
        scheme_id: str,
        level_id: str,
        member_type: str,
        parameter: str,
        *,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Add a member to an issue security level.

        Args:
            scheme_id: Numeric issue security scheme ID.
            level_id: Numeric issue security level ID.
            member_type: One of ``reporter``, ``group``, ``user``,
                ``projectrole``, ``applicationRole``, ``allUsers``.
            parameter: Group name, account ID, role ID or application role ID.
            dry_run: When ``True`` (the default), report what would change
                without calling the API.

        Returns:
            The dry-run preview, or the result of the add when executed.
        """
        scheme = _require_numeric_id("scheme_id", scheme_id)
        level = _require_numeric_id("level_id", level_id)
        if member_type not in _SECURITY_MEMBER_TYPES:
            msg = (
                f"Invalid member_type {member_type!r}; expected one of "
                f"{', '.join(sorted(_SECURITY_MEMBER_TYPES))}"
            )
            raise ValueError(msg)
        member_parameter = _require_id("parameter", parameter)

        endpoint = f"rest/api/3/issuesecurityschemes/{scheme}/level/{level}/member"
        payload = {"members": [{"type": member_type, "parameter": member_parameter}]}

        if dry_run:
            existing = self.get_security_level_members(level)
            duplicate = [
                member
                for member in existing["members"]
                if member.get("type") == member_type
                and member.get("parameter") == member_parameter
            ]
            preview = _preview(
                "set_issue_security_level_member", endpoint, payload, existing
            )
            preview["duplicate"] = bool(duplicate)
            preview["existing_member_ids"] = [
                member["member_id"] for member in duplicate
            ]
            return preview

        self.jira.put(endpoint, data=payload)
        return {
            "success": True,
            "dry_run": False,
            "action": "set_issue_security_level_member",
            "scheme_id": scheme,
            "level_id": level,
            "member": {"type": member_type, "parameter": member_parameter},
        }

    def remove_issue_security_level_member(
        self,
        scheme_id: str,
        level_id: str,
        member_id: str,
        *,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Remove a member from an issue security level.

        Args:
            scheme_id: Numeric issue security scheme ID.
            level_id: Numeric issue security level ID.
            member_id: Numeric ID of the level member to remove.
            dry_run: When ``True`` (the default), report what would change
                without calling the API.

        Returns:
            The dry-run preview, or the removal result when executed.
        """
        scheme = _require_numeric_id("scheme_id", scheme_id)
        level = _require_numeric_id("level_id", level_id)
        member = _require_numeric_id("member_id", member_id)
        endpoint = (
            f"rest/api/3/issuesecurityschemes/{scheme}/level/{level}/member/{member}"
        )

        if dry_run:
            current = self.get_security_level_members(level)
            target = next(
                (m for m in current["members"] if m["member_id"] == member), None
            )
            if target is None:
                msg = f"Member {member} is not a member of level {level}"
                raise ValueError(msg)
            return _preview("remove_issue_security_level_member", endpoint, {}, target)

        self.jira.delete(endpoint)
        return {
            "success": True,
            "dry_run": False,
            "action": "remove_issue_security_level_member",
            "scheme_id": scheme,
            "level_id": level,
            "member_id": member,
        }

    def set_default_issue_security_level(
        self,
        scheme_id: str,
        level_id: str | None,
        *,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Set the default issue security level for a scheme.

        Passing ``None`` as ``level_id`` resets the default level.

        Args:
            scheme_id: Numeric issue security scheme ID.
            level_id: Numeric issue security level ID, or ``None`` to reset.
            dry_run: When ``True`` (the default), report what would change
                without calling the API.

        Returns:
            The dry-run preview, or the result of the update when executed.
        """
        scheme = _require_numeric_id("scheme_id", scheme_id)
        level = _require_numeric_id("level_id", level_id) if level_id else None

        endpoint = "rest/api/3/issuesecurityschemes/level/default"
        payload = {
            "defaultValues": [
                {"issueSecuritySchemeId": scheme, "defaultLevelId": level}
            ]
        }

        if dry_run:
            schemes = {s["id"]: s for s in self.get_issue_security_schemes()}
            current = schemes.get(scheme)
            if current is None:
                msg = f"Issue security scheme {scheme} not found"
                raise ValueError(msg)
            return _preview(
                "set_default_issue_security_level", endpoint, payload, current
            )

        self.jira.put(endpoint, data=payload)
        return {
            "success": True,
            "dry_run": False,
            "action": "set_default_issue_security_level",
            "scheme_id": scheme,
            "default_level_id": level,
        }

    def set_issue_security(
        self,
        issue_key: str,
        level_id: str | None,
        *,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Set the security level of an issue.

        Passing ``None`` as ``level_id`` clears the level. This is the operation
        that requires the ``SET_ISSUE_SECURITY`` permission in the project's
        permission scheme.

        Args:
            issue_key: The issue key, e.g. ``BB5O-123``.
            level_id: Numeric issue security level ID, or ``None`` to clear.
            dry_run: When ``True`` (the default), report what would change
                without calling the API.

        Returns:
            The dry-run preview, or the updated issue security field when
            executed.
        """
        issue = _require_id("issue_key", issue_key)
        level = _require_numeric_id("level_id", level_id) if level_id else None

        endpoint = f"rest/api/3/issue/{issue}"
        payload = {"fields": {"security": {"id": level}}}

        if dry_run:
            current_issue = self._expect_dict(self.jira.get(endpoint), endpoint)
            current = (current_issue.get("fields") or {}).get("security")
            return _preview("set_issue_security", endpoint, payload, current)

        self.jira.put(endpoint, data=payload)
        return {
            "success": True,
            "dry_run": False,
            "action": "set_issue_security",
            "issue_key": issue,
            "security_level_id": level,
        }
