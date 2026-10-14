"""Module for Jira issue security scheme operations.

All endpoints are Jira Cloud REST API v3 and require the *Administer Jira*
global permission (OAuth scope ``manage:jira-configuration`` or an admin API
token). Write operations are gated by ``check_write_access`` in the server layer.

Every write operation supports ``dry_run``, which defaults to ``True``: the
method reports the current state and the payload that would be sent without
calling the API.

Response shapes follow Atlassian's OpenAPI definition and were checked against
a live Jira Cloud site.
"""

import logging
from typing import Any
from urllib.parse import urlencode, urlsplit

from requests.exceptions import HTTPError

from ..exceptions import MCPAtlassianAuthenticationError
from .permissions import (
    AdminHelpersMixin,
    _holder_summary,
    _preview,
    _require_id,
    _require_numeric_id,
    _require_text,
)

logger = logging.getLogger("mcp-jira")

# Member types documented for
# PUT /rest/api/3/issuesecurityschemes/{schemeId}/level/{levelId}/member,
# mapped to whether a ``parameter`` is required. The documented spelling of the
# project role type is lowercase, although GET responses return ``projectRole``.
_SECURITY_MEMBER_TYPES: dict[str, bool] = {
    "reporter": False,
    "group": True,
    "user": True,
    "projectrole": True,
    "applicationRole": True,
}
_MEMBER_TYPE_ALIASES = {"projectRole": "projectrole"}


class SecurityMixin(AdminHelpersMixin):
    """Mixin for Jira issue security scheme operations."""

    # ------------------------------------------------------------------
    # Read operations
    # ------------------------------------------------------------------

    def _get_security_scheme(self, scheme_id: str) -> dict[str, Any]:
        """GET ``/issuesecurityschemes/{id}`` and normalize the result."""
        scheme = _require_numeric_id("scheme_id", scheme_id)
        endpoint = f"rest/api/3/issuesecurityschemes/{scheme}"
        body = self._expect_dict(self.jira.get(endpoint), endpoint)
        default_level = body.get("defaultSecurityLevelId")
        return {
            "id": str(body.get("id", scheme)),
            "name": body.get("name"),
            "description": body.get("description"),
            "default_level_id": None if default_level is None else str(default_level),
            "levels": [
                {
                    "id": str(level.get("id", "")),
                    "name": level.get("name"),
                    "description": level.get("description"),
                }
                for level in body.get("levels") or []
            ],
        }

    def get_issue_security_schemes(self) -> list[dict[str, Any]]:
        """List all issue security schemes.

        Returns:
            List of schemes with ``id``, ``name``, ``description`` and
            ``default_level_id`` (``None`` when the scheme has no default).
        """
        endpoint = "rest/api/3/issuesecurityschemes"
        entries = self._expect_key(
            self.jira.get(endpoint), "issueSecuritySchemes", endpoint
        )
        schemes = []
        for entry in entries:
            default_level = entry.get("defaultSecurityLevelId")
            schemes.append(
                {
                    "id": str(entry.get("id", "")),
                    "name": entry.get("name"),
                    "description": entry.get("description"),
                    "default_level_id": (
                        None if default_level is None else str(default_level)
                    ),
                }
            )
        return schemes

    def get_issue_security_scheme_for_project(self, project_key: str) -> dict[str, Any]:
        """Find the issue security scheme associated with a project.

        Args:
            project_key: The project key or numeric project ID.

        Returns:
            Dict with ``project_id``, ``scheme_id``, ``name``, ``description``,
            ``default_level_id`` and ``levels``. A project without a security
            scheme returns ``scheme_id: None`` and no levels.
        """
        project_id = self._resolve_project_id(project_key)
        endpoint = "rest/api/3/issuesecurityschemes/project"
        rows = self._get_all_pages(endpoint, params={"projectId": [project_id]})
        scheme_id = next(
            (
                str(row["issueSecuritySchemeId"])
                for row in rows
                if row.get("issueSecuritySchemeId")
            ),
            None,
        )
        if scheme_id is None:
            return {
                "project_id": project_id,
                "scheme_id": None,
                "name": None,
                "description": None,
                "default_level_id": None,
                "levels": [],
            }

        scheme = self._get_security_scheme(scheme_id)
        return {
            "project_id": project_id,
            "scheme_id": scheme["id"],
            "name": scheme["name"],
            "description": scheme["description"],
            "default_level_id": scheme["default_level_id"],
            "levels": scheme["levels"],
        }

    def get_issue_security_levels(self, scheme_id: str) -> list[dict[str, Any]]:
        """List the security levels of an issue security scheme.

        Args:
            scheme_id: Numeric issue security scheme ID.

        Returns:
            List of levels with ``id``, ``name``, ``description`` and
            ``is_default``. These IDs can be passed to
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
                "is_default": bool(entry.get("isDefault")),
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
            Each member has ``member_id``, ``type``, ``parameter``, ``value`` and
            ``name`` (when Jira can resolve it). ``member_id`` is what
            ``remove_issue_security_level_member`` needs.

        Raises:
            MCPAtlassianAuthenticationError: On 401/403 responses.
        """
        level = _require_numeric_id("level_id", level_id)
        try:
            values = self._get_all_pages(
                "rest/api/3/issuesecurityschemes/level/member",
                params={"levelId": [level], "expand": "all"},
            )
            members = []
            for entry in values:
                holder = _holder_summary(entry.get("holder"))
                members.append(
                    {
                        "member_id": str(entry.get("id", "")),
                        "type": holder["type"],
                        "parameter": holder["parameter"],
                        "value": holder["value"],
                        "name": holder["name"],
                    }
                )
            return {
                "success": True,
                "level_id": level,
                "total": len(members),
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
                f"Error fetching security level members for level {level}: {e}"
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
            mappings.append(
                {
                    "oldLevelId": _require_numeric_id(
                        "oldLevelId", mapping.get("oldLevelId")
                    ),
                    "newLevelId": _require_numeric_id(
                        "newLevelId", mapping.get("newLevelId")
                    ),
                }
            )

        endpoint = "rest/api/3/issuesecurityschemes/project"
        payload: dict[str, Any] = {"projectId": project_id, "schemeId": scheme}
        if mappings:
            payload["oldToNewSecurityLevelMappings"] = mappings

        if dry_run:
            current = self.get_issue_security_scheme_for_project(project_id)
            mapped = {m["oldLevelId"] for m in mappings}
            unmapped = sorted(
                level["id"] for level in current["levels"] if level["id"] not in mapped
            )
            preview = _preview(
                "set_project_issue_security_scheme", endpoint, payload, current
            )
            preview["unmapped_levels"] = unmapped
            if scheme:
                preview["new_scheme_levels"] = self.get_issue_security_levels(scheme)
            preview["note"] = (
                "Executing this operation is asynchronous; the response returns "
                "a task location to poll with get_jira_task."
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
        parameter: str | None = None,
        *,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Add a member to an issue security level.

        Args:
            scheme_id: Numeric issue security scheme ID.
            level_id: Numeric issue security level ID.
            member_type: One of ``reporter``, ``group``, ``user``,
                ``projectrole`` (``projectRole`` is accepted too) or
                ``applicationRole``.
            parameter: Group name, account ID, project role ID or application
                role key. Not used by ``reporter``.
            dry_run: When ``True`` (the default), report what would change
                without calling the API.

        Returns:
            The dry-run preview, or the result of the add when executed.
        """
        scheme = _require_numeric_id("scheme_id", scheme_id)
        level = _require_numeric_id("level_id", level_id)
        member_type = _MEMBER_TYPE_ALIASES.get(member_type, member_type)
        if member_type not in _SECURITY_MEMBER_TYPES:
            msg = (
                f"Invalid member_type {member_type!r}; expected one of "
                f"{', '.join(sorted(_SECURITY_MEMBER_TYPES))}"
            )
            raise ValueError(msg)

        member: dict[str, str] = {"type": member_type}
        if parameter:
            member["parameter"] = _require_text("parameter", parameter)
        elif _SECURITY_MEMBER_TYPES[member_type]:
            msg = f"member_type {member_type!r} needs a parameter"
            raise ValueError(msg)

        endpoint = f"rest/api/3/issuesecurityschemes/{scheme}/level/{level}/member"
        payload = {"members": [member]}

        if dry_run:
            existing = self.get_security_level_members(level)
            duplicate = [
                entry
                for entry in existing["members"]
                if str(entry.get("type")).lower() == member_type.lower()
                and (
                    "parameter" not in member
                    or member["parameter"]
                    in (entry.get("parameter"), entry.get("value"))
                )
            ]
            preview = _preview(
                "set_issue_security_level_member", endpoint, payload, existing
            )
            preview["duplicate"] = bool(duplicate)
            preview["existing_member_ids"] = [entry["member_id"] for entry in duplicate]
            return preview

        self.jira.put(endpoint, data=payload)
        return {
            "success": True,
            "dry_run": False,
            "action": "set_issue_security_level_member",
            "scheme_id": scheme,
            "level_id": level,
            "member": member,
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
            member_id: Numeric ID of the level member to remove, from
                ``get_security_level_members``.
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
            current = self._get_security_scheme(scheme)
            if level and level not in {entry["id"] for entry in current["levels"]}:
                msg = f"Level {level} does not belong to issue security scheme {scheme}"
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
        payload = {"fields": {"security": {"id": level} if level else None}}

        if dry_run:
            query = urlencode({"fields": "security"})
            current_issue = self._expect_dict(
                self.jira.get(f"{endpoint}?{query}"), endpoint
            )
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
