"""Module for Jira screen, field context and field configuration operations.

All endpoints are Jira Cloud REST API v3 and require the *Administer Jira*
global permission (OAuth scope ``manage:jira-configuration`` or an admin API
token). Write operations are gated by ``check_write_access`` in the server layer.
"""

import logging
import re
from typing import Any
from urllib.parse import urlencode

from .client import JiraClient

logger = logging.getLogger("mcp-jira")

_ID_PATTERN = re.compile(r"^[A-Za-z0-9_\-]+$")
_PAGE_SIZE = 100
_PROJECT_BATCH_SIZE = 50


def _require_id(name: str, value: Any) -> str:
    """Validate an identifier before it is interpolated into a URL path.

    Args:
        name: Parameter name, used in the error message.
        value: The value to validate.

    Returns:
        The identifier as a string.

    Raises:
        ValueError: If the value is empty or contains characters outside
            ``[A-Za-z0-9_-]``.
    """
    text = str(value).strip() if value is not None else ""
    if not _ID_PATTERN.match(text):
        msg = f"Invalid {name}: {value!r}"
        raise ValueError(msg)
    return text


class ScreensMixin(JiraClient):
    """Mixin for Jira screen, field context and field configuration operations."""

    _field_name_to_id_map: dict[str, str] | None

    # ------------------------------------------------------------------
    # Helpers
    # ------------------------------------------------------------------

    def _get_all_pages(
        self, path: str, params: dict[str, Any] | None = None
    ) -> list[dict[str, Any]]:
        """GET every page of a ``startAt``/``maxResults`` paginated endpoint.

        The query string is built here because Jira expects array parameters
        to be repeated (``projectId=1&projectId=2``), which the underlying
        client does not do for list values.

        Args:
            path: API path relative to the Jira base URL.
            params: Query parameters; list values are repeated.

        Returns:
            The concatenated ``values`` of all pages.
        """
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

    def _invalidate_field_cache(self) -> None:
        self._field_ids_cache = None
        self._field_name_to_id_map = None

    def _resolve_project_ids(self, projects: list[str]) -> dict[str, str]:
        """Map project keys or numeric IDs to numeric project IDs.

        Args:
            projects: Project keys (e.g. ``BB5``) or numeric project IDs.

        Returns:
            Dict of the original input value to the numeric project ID.
        """
        resolved: dict[str, str] = {}
        for entry in projects:
            value = _require_id("project", entry)
            if value.isdigit():
                resolved[value] = value
                continue
            project = self._expect_dict(
                self.jira.get(f"rest/api/3/project/{value}"), "project"
            )
            resolved[value] = str(project["id"])
        return resolved

    # ------------------------------------------------------------------
    # Issue type screen schemes and screen schemes
    # ------------------------------------------------------------------

    def get_project_screen_schemes(
        self, projects: list[str], *, include_screens: bool = False
    ) -> dict[str, Any]:
        """Find the screen configuration used by one or more projects.

        Jira has no "used by" lookup for screens, so shared screens are found
        by resolving several projects at once and grouping the result. Only
        the projects passed in are considered.

        Args:
            projects: Project keys or numeric IDs.
            include_screens: Also resolve each screen scheme to its
                default/create/edit/view screens and build ``screen_usage``,
                which lists which of the given projects use each screen.

        Returns:
            Dict with ``projects`` (issue type screen scheme and issue type
            mappings per project) and, when ``include_screens`` is set,
            ``screen_schemes`` and ``screen_usage``. Team-managed projects
            have no issue type screen scheme and get ``None``.
        """
        resolved = self._resolve_project_ids(projects)
        project_id_to_labels: dict[str, list[str]] = {}
        for label, project_id in resolved.items():
            project_id_to_labels.setdefault(project_id, []).append(label)

        scheme_by_project: dict[str, dict[str, Any]] = {}
        ids = list(project_id_to_labels)
        for i in range(0, len(ids), _PROJECT_BATCH_SIZE):
            batch = ids[i : i + _PROJECT_BATCH_SIZE]
            entries = self._get_all_pages(
                "rest/api/3/issuetypescreenscheme/project", {"projectId": batch}
            )
            for entry in entries:
                for project_id in entry.get("projectIds", []):
                    scheme_by_project[str(project_id)] = entry["issueTypeScreenScheme"]

        scheme_ids = sorted({str(s["id"]) for s in scheme_by_project.values()})
        mappings_by_scheme: dict[str, list[dict[str, str]]] = {
            scheme_id: [] for scheme_id in scheme_ids
        }
        if scheme_ids:
            mappings = self._get_all_pages(
                "rest/api/3/issuetypescreenscheme/mapping",
                {"issueTypeScreenSchemeId": scheme_ids},
            )
            for item in mappings:
                mappings_by_scheme[str(item["issueTypeScreenSchemeId"])].append(
                    {
                        "issue_type_id": str(item["issueTypeId"]),
                        "screen_scheme_id": str(item["screenSchemeId"]),
                    }
                )

        project_entries: list[dict[str, Any]] = []
        for label, project_id in resolved.items():
            scheme = scheme_by_project.get(project_id)
            project_entries.append(
                {
                    "project": label,
                    "project_id": project_id,
                    "issue_type_screen_scheme": scheme,
                    "issue_type_mappings": (
                        mappings_by_scheme[str(scheme["id"])] if scheme else []
                    ),
                }
            )
        result: dict[str, Any] = {"projects": project_entries}

        if include_screens:
            screen_scheme_ids = sorted(
                {
                    m["screen_scheme_id"]
                    for entry in project_entries
                    for m in entry["issue_type_mappings"]
                }
            )
            schemes = self.get_screen_schemes(screen_scheme_ids)
            result["screen_schemes"] = schemes
            result["screen_usage"] = self._build_screen_usage(project_entries, schemes)
        return result

    @staticmethod
    def _build_screen_usage(
        project_entries: list[dict[str, Any]], screen_schemes: list[dict[str, Any]]
    ) -> dict[str, dict[str, list[str]]]:
        screens_by_scheme: dict[str, set[str]] = {
            str(s["id"]): {str(v) for v in (s.get("screens") or {}).values()}
            for s in screen_schemes
        }
        usage: dict[str, dict[str, set[str]]] = {}
        for entry in project_entries:
            for mapping in entry["issue_type_mappings"]:
                scheme_id = mapping["screen_scheme_id"]
                for screen_id in screens_by_scheme.get(scheme_id, set()):
                    bucket = usage.setdefault(
                        screen_id, {"projects": set(), "screen_schemes": set()}
                    )
                    bucket["projects"].add(entry["project"])
                    bucket["screen_schemes"].add(scheme_id)
        return {
            screen_id: {key: sorted(values) for key, values in bucket.items()}
            for screen_id, bucket in sorted(usage.items())
        }

    def get_screen_schemes(self, screen_scheme_ids: list[str]) -> list[dict[str, Any]]:
        """Get screen schemes with their default/create/edit/view screen IDs.

        Args:
            screen_scheme_ids: IDs of the screen schemes to fetch.

        Returns:
            Screen scheme dicts with ``id``, ``name``, ``description`` and
            ``screens``. Empty if no IDs are given.
        """
        if not screen_scheme_ids:
            return []
        ids = [_require_id("screen_scheme_id", i) for i in screen_scheme_ids]
        return self._get_all_pages("rest/api/3/screenscheme", {"id": ids})

    # ------------------------------------------------------------------
    # Screens, tabs and tab fields
    # ------------------------------------------------------------------

    def get_screen_tabs(
        self, screen_id: str, project_key: str | None = None
    ) -> list[dict[str, Any]]:
        """Get the tabs of a screen.

        Args:
            screen_id: Screen ID.
            project_key: Optional project key; tabs are then resolved for that
                project's screen.

        Returns:
            List of tab dicts with ``id`` and ``name``.
        """
        screen = _require_id("screen_id", screen_id)
        params = (
            {"projectKey": _require_id("project_key", project_key)}
            if project_key
            else None
        )
        endpoint = f"rest/api/3/screens/{screen}/tabs"
        return self._expect_list(self.jira.get(endpoint, params=params), endpoint)

    def get_screen_tab_fields(
        self, screen_id: str, tab_id: str
    ) -> list[dict[str, Any]]:
        """Get the fields on a screen tab.

        Args:
            screen_id: Screen ID.
            tab_id: Tab ID.

        Returns:
            List of field dicts with ``id`` and ``name``.
        """
        screen = _require_id("screen_id", screen_id)
        tab = _require_id("tab_id", tab_id)
        endpoint = f"rest/api/3/screens/{screen}/tabs/{tab}/fields"
        return self._expect_list(self.jira.get(endpoint), endpoint)

    def get_screen_available_fields(self, screen_id: str) -> list[dict[str, Any]]:
        """Get the fields that can still be added to a screen.

        Args:
            screen_id: Screen ID.

        Returns:
            List of field dicts with ``id`` and ``name``.
        """
        screen = _require_id("screen_id", screen_id)
        endpoint = f"rest/api/3/screens/{screen}/availableFields"
        return self._expect_list(self.jira.get(endpoint), endpoint)

    def add_field_to_screen_tab(
        self, screen_id: str, tab_id: str, field_id: str
    ) -> dict[str, Any]:
        """Add a field to a screen tab.

        This changes every project that shares the screen.

        Args:
            screen_id: Screen ID.
            tab_id: Tab ID.
            field_id: Field ID (e.g. ``customfield_10914``).

        Returns:
            The added field as ``{"id": ..., "name": ...}``.
        """
        screen = _require_id("screen_id", screen_id)
        tab = _require_id("tab_id", tab_id)
        field = _require_id("field_id", field_id)
        endpoint = f"rest/api/3/screens/{screen}/tabs/{tab}/fields"
        logger.info(f"Adding field {field} to screen {screen} tab {tab}")
        return self._expect_dict(
            self.jira.post(endpoint, json={"fieldId": field}), endpoint
        )

    def remove_field_from_screen_tab(
        self, screen_id: str, tab_id: str, field_id: str
    ) -> None:
        """Remove a field from a screen tab.

        Args:
            screen_id: Screen ID.
            tab_id: Tab ID.
            field_id: Field ID to remove.
        """
        screen = _require_id("screen_id", screen_id)
        tab = _require_id("tab_id", tab_id)
        field = _require_id("field_id", field_id)
        logger.info(f"Removing field {field} from screen {screen} tab {tab}")
        self.jira.delete(f"rest/api/3/screens/{screen}/tabs/{tab}/fields/{field}")

    # ------------------------------------------------------------------
    # Custom field contexts
    # ------------------------------------------------------------------

    def get_field_contexts(self, field_id: str) -> list[dict[str, Any]]:
        """Get all contexts of a custom field.

        Args:
            field_id: Custom field ID (e.g. ``customfield_10914``).

        Returns:
            Context dicts with ``id``, ``name``, ``description``,
            ``isGlobalContext`` and ``isAnyIssueType``.
        """
        field = _require_id("field_id", field_id)
        return self._get_all_pages(f"rest/api/3/field/{field}/context")

    def get_field_context_project_mapping(
        self, field_id: str, context_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """Get which projects each context of a custom field applies to.

        Args:
            field_id: Custom field ID.
            context_ids: Limit to these contexts. None returns all contexts.

        Returns:
            Mapping dicts with ``contextId``, ``projectId`` and
            ``isGlobalContext``. A global context has no ``projectId``.
        """
        field = _require_id("field_id", field_id)
        params = (
            {"contextId": [_require_id("context_id", c) for c in context_ids]}
            if context_ids
            else None
        )
        return self._get_all_pages(
            f"rest/api/3/field/{field}/context/projectmapping", params
        )

    def create_field_context(
        self,
        field_id: str,
        name: str,
        description: str | None = None,
        project_ids: list[str] | None = None,
        issue_type_ids: list[str] | None = None,
    ) -> dict[str, Any]:
        """Create a custom field context.

        Empty ``project_ids`` creates a global context; empty
        ``issue_type_ids`` applies the context to all issue types.

        Args:
            field_id: Custom field ID.
            name: Context name.
            description: Context description.
            project_ids: Numeric project IDs the context applies to.
            issue_type_ids: Issue type IDs the context applies to.

        Returns:
            The created context, including its ``id``.
        """
        field = _require_id("field_id", field_id)
        payload: dict[str, Any] = {"name": name}
        if description is not None:
            payload["description"] = description
        if project_ids:
            payload["projectIds"] = [_require_id("project_id", p) for p in project_ids]
        if issue_type_ids:
            payload["issueTypeIds"] = [
                _require_id("issue_type_id", i) for i in issue_type_ids
            ]
        endpoint = f"rest/api/3/field/{field}/context"
        logger.info(f"Creating context '{name}' for field {field}")
        return self._expect_dict(self.jira.post(endpoint, json=payload), endpoint)

    def assign_context_to_project(
        self, field_id: str, context_id: str, project_ids: list[str]
    ) -> None:
        """Assign a custom field context to projects.

        Jira rejects the request if any of the projects is already assigned
        to a context of the same field.

        Args:
            field_id: Custom field ID.
            context_id: Context ID.
            project_ids: Numeric project IDs to assign.
        """
        field = _require_id("field_id", field_id)
        context = _require_id("context_id", context_id)
        if not project_ids:
            raise ValueError("project_ids must contain at least one project ID")
        payload = {"projectIds": [_require_id("project_id", p) for p in project_ids]}
        logger.info(f"Assigning context {context} of field {field} to projects")
        self.jira.put(
            f"rest/api/3/field/{field}/context/{context}/project", data=payload
        )

    # ------------------------------------------------------------------
    # Fields and field configurations
    # ------------------------------------------------------------------

    def create_custom_field(
        self,
        name: str,
        field_type: str,
        description: str | None = None,
        searcher_key: str | None = None,
    ) -> dict[str, Any]:
        """Create a custom field.

        Args:
            name: Display name (not unique).
            field_type: Full type key, e.g.
                ``com.atlassian.jira.plugin.system.customfieldtypes:url``,
                ``:textfield`` or ``:textarea``.
            description: Field description.
            searcher_key: Optional searcher key.

        Returns:
            The created field definition.
        """
        payload: dict[str, Any] = {"name": name, "type": field_type}
        if description is not None:
            payload["description"] = description
        if searcher_key is not None:
            payload["searcherKey"] = searcher_key
        logger.info(f"Creating custom field '{name}' of type {field_type}")
        result = self._expect_dict(
            self.jira.post("rest/api/3/field", json=payload), "field"
        )
        self._invalidate_field_cache()
        return result

    def update_field(
        self,
        field_id: str,
        name: str | None = None,
        description: str | None = None,
    ) -> None:
        """Update the name and/or description of a custom field.

        Args:
            field_id: Custom field ID.
            name: New name.
            description: New description.

        Raises:
            ValueError: If neither name nor description is given.
        """
        field = _require_id("field_id", field_id)
        payload: dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        if description is not None:
            payload["description"] = description
        if not payload:
            raise ValueError("Provide at least one of name or description")
        logger.info(f"Updating custom field {field}")
        self.jira.put(f"rest/api/3/field/{field}", data=payload)
        self._invalidate_field_cache()

    def get_project_field_configuration_scheme(
        self, projects: list[str]
    ) -> list[dict[str, Any]]:
        """Get the field configuration scheme used by projects.

        Args:
            projects: Project keys or numeric IDs.

        Returns:
            Entries with ``fieldConfigurationScheme`` (absent when the project
            uses the default configuration) and ``projectIds``.
        """
        project_ids = list(dict.fromkeys(self._resolve_project_ids(projects).values()))
        return self._get_all_pages(
            "rest/api/3/fieldconfigurationscheme/project", {"projectId": project_ids}
        )

    def get_field_configuration_items(
        self, field_configuration_id: str
    ) -> list[dict[str, Any]]:
        """Get the fields of a field configuration (hidden/required state).

        Args:
            field_configuration_id: Field configuration ID.

        Returns:
            Items with ``id``, ``isHidden``, ``isRequired``, ``description``
            and ``renderer``.
        """
        configuration = _require_id("field_configuration_id", field_configuration_id)
        return self._get_all_pages(
            f"rest/api/3/fieldconfiguration/{configuration}/fields"
        )
