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

    def list_screens(
        self, query: str | None = None, start_at: int = 0, max_results: int = 100
    ) -> dict[str, Any]:
        """Search for screens.

        Args:
            query: Optional screen name query.
            start_at: Zero-based result offset.
            max_results: Maximum results to request.

        Returns:
            Jira's paginated screen search response.
        """
        params: dict[str, Any] = {
            "startAt": start_at,
            "maxResults": min(max_results, 100),
        }
        if query:
            # The API silently ignores an unknown "query" parameter and would
            # return every screen, so the documented name must be used.
            params["queryString"] = query
        return self._expect_dict(
            self.jira.get("rest/api/3/screens", params=params), "screens"
        )

    # ------------------------------------------------------------------
    # Screens, tabs and tab fields
    # ------------------------------------------------------------------

    def create_screen(
        self, name: str, description: str | None = None
    ) -> dict[str, Any]:
        """Create a screen.

        Screens are created without tabs (Jira adds a default "Field Tab") and
        are not used by any project until a screen scheme references them.

        Args:
            name: Screen name; must be unique. Max 255 characters.
            description: Optional screen description.

        Returns:
            The created screen, including its numeric ``id``.
        """
        payload: dict[str, Any] = {"name": name}
        if description is not None:
            payload["description"] = description
        logger.info(f"Creating screen '{name}'")
        return self._expect_dict(
            self.jira.post("rest/api/3/screens", json=payload), "screens"
        )

    def add_screen_tab(self, screen_id: str, tab_name: str) -> dict[str, Any]:
        """Add a tab to a screen.

        Args:
            screen_id: Screen ID.
            tab_name: Name of the new tab.

        Returns:
            The created tab, including its numeric ``id``.
        """
        screen = _require_id("screen_id", screen_id)
        endpoint = f"rest/api/3/screens/{screen}/tabs"
        logger.info(f"Adding tab '{tab_name}' to screen {screen}")
        return self._expect_dict(
            self.jira.post(endpoint, json={"name": tab_name}), endpoint
        )

    def _get_screen_by_id(self, screen_id: str) -> dict[str, Any] | None:
        """Fetch a single screen by its numeric ID.

        There is no ``GET /screens/{id}`` endpoint, so the list endpoint's
        ``id`` filter is used instead.

        Args:
            screen_id: Numeric screen ID.

        Returns:
            The screen dict, or None when it does not exist.
        """
        screen = _require_id("screen_id", screen_id)
        query = urlencode({"id": [screen]}, doseq=True)
        page = self._expect_dict(
            self.jira.get(f"rest/api/3/screens?{query}"), "screens"
        )
        values = page.get("values", [])
        return values[0] if values else None

    def copy_screen(
        self, source_screen_id: str, new_name: str, description: str | None = None
    ) -> dict[str, Any]:
        """Copy a screen with its tabs and fields, preserving field order.

        There is no REST endpoint for copying screens, so this creates a new
        screen and re-adds every tab and field from the source screen.

        Args:
            source_screen_id: ID of the screen to copy.
            new_name: Name for the new screen; must be unique.
            description: Optional description; defaults to the source screen's.

        Returns:
            The created screen plus a ``copy_summary`` with the tabs and
            fields that were copied.

        Raises:
            ValueError: If the source screen does not exist.
        """
        source = _require_id("source_screen_id", source_screen_id)
        source_screen = self._get_screen_by_id(source)
        if source_screen is None:
            raise ValueError(f"Source screen {source} does not exist")
        description_to_use = (
            description if description is not None else source_screen.get("description")
        )
        created = self.create_screen(new_name, description_to_use or None)

        tabs: list[dict[str, Any]] = []
        copied_fields = 0
        for tab in self.get_screen_tabs(source):
            new_tab = self.add_screen_tab(str(created["id"]), str(tab["name"]))
            fields = self.get_screen_tab_fields(source, str(tab["id"]))
            for field in fields:
                self.add_field_to_screen_tab(
                    str(created["id"]), str(new_tab["id"]), str(field["id"])
                )
                copied_fields += 1
            tabs.append({"tab": tab["name"], "fields": len(fields)})

        created["copy_summary"] = {
            "source_screen_id": source,
            "tabs": tabs,
            "fields_copied": copied_fields,
        }
        return created

    def update_screen_scheme(
        self,
        screen_scheme_id: str,
        name: str | None = None,
        description: str | None = None,
        *,
        default_screen_id: str | None = None,
        create_screen_id: str | None = None,
        edit_screen_id: str | None = None,
        view_screen_id: str | None = None,
    ) -> None:
        """Update a screen scheme.

        Only the values that are supplied are changed; omitted screens keep
        their current assignment. This affects every project that uses the
        scheme.

        Args:
            screen_scheme_id: Screen scheme ID.
            name: New scheme name.
            description: New scheme description.
            default_screen_id: Screen ID for the default screen.
            create_screen_id: Screen ID for the create screen.
            edit_screen_id: Screen ID for the edit screen.
            view_screen_id: Screen ID for the view screen.

        Raises:
            ValueError: If no field to update is given.
        """
        scheme = _require_id("screen_scheme_id", screen_scheme_id)
        payload: dict[str, Any] = {}
        if name is not None:
            payload["name"] = name
        if description is not None:
            payload["description"] = description
        screens: dict[str, str] = {}
        if default_screen_id is not None:
            screens["default"] = _require_id("default_screen_id", default_screen_id)
        if create_screen_id is not None:
            screens["create"] = _require_id("create_screen_id", create_screen_id)
        if edit_screen_id is not None:
            screens["edit"] = _require_id("edit_screen_id", edit_screen_id)
        if view_screen_id is not None:
            screens["view"] = _require_id("view_screen_id", view_screen_id)
        if screens:
            payload["screens"] = screens
        if not payload:
            raise ValueError("Provide at least one field to update")
        logger.info(f"Updating screen scheme {scheme}")
        self.jira.put(f"rest/api/3/screenscheme/{scheme}", data=payload)

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
    # Custom field options
    # ------------------------------------------------------------------

    def get_field_options(
        self, field_id: str, context_id: str, *, option_id: str | None = None
    ) -> list[dict[str, Any]] | dict[str, Any]:
        """Get options belonging to a custom field context.

        Args:
            field_id: Custom field ID.
            context_id: Field context ID.
            option_id: Optional option ID for a single option lookup.

        Returns:
            A list of options, or one option when ``option_id`` is supplied.
        """
        field = _require_id("field_id", field_id)
        context = _require_id("context_id", context_id)
        suffix = f"/{_require_id('option_id', option_id)}" if option_id else ""
        endpoint = f"rest/api/3/field/{field}/context/{context}/option{suffix}"
        if option_id:
            return self._expect_dict(self.jira.get(endpoint), endpoint)
        return self._get_all_pages(endpoint)

    def add_field_options(
        self,
        field_id: str,
        context_id: str,
        options: list[dict[str, Any]],
    ) -> list[dict[str, Any]]:
        """Add options to a custom field context."""
        if not options:
            raise ValueError("options must contain at least one option")
        field = _require_id("field_id", field_id)
        context = _require_id("context_id", context_id)
        endpoint = f"rest/api/3/field/{field}/context/{context}/option"
        result = self._expect_dict(
            self.jira.post(endpoint, json={"options": options}), endpoint
        )
        return result.get("options", [])

    def update_field_option(
        self,
        field_id: str,
        context_id: str,
        option_id: str,
        *,
        value: str | None = None,
        disabled: bool | None = None,
    ) -> dict[str, Any]:
        """Update a custom field option's value and/or disabled state."""
        if value is None and disabled is None:
            raise ValueError("Provide at least one of value or disabled")
        field = _require_id("field_id", field_id)
        context = _require_id("context_id", context_id)
        option = _require_id("option_id", option_id)
        payload: dict[str, Any] = {}
        if value is not None:
            payload["value"] = value
        if disabled is not None:
            payload["disabled"] = disabled
        endpoint = f"rest/api/3/field/{field}/context/{context}/option/{option}"
        return self._expect_dict(self.jira.put(endpoint, data=payload), endpoint)

    def remove_field_option(
        self, field_id: str, context_id: str, option_id: str
    ) -> None:
        """Delete a custom field option."""
        field = _require_id("field_id", field_id)
        context = _require_id("context_id", context_id)
        option = _require_id("option_id", option_id)
        self.jira.delete(f"rest/api/3/field/{field}/context/{context}/option/{option}")

    def reorder_field_options(
        self,
        field_id: str,
        context_id: str,
        option_ids: list[str],
        *,
        position: str = "First",
    ) -> dict[str, Any]:
        """Move field options to a position in the option list."""
        if not option_ids:
            raise ValueError("option_ids must contain at least one option ID")
        field = _require_id("field_id", field_id)
        context = _require_id("context_id", context_id)
        payload = {
            "customFieldOptionIds": [_require_id("option_id", i) for i in option_ids],
            "position": position,
        }
        endpoint = f"rest/api/3/field/{field}/context/{context}/option/move"
        return self._expect_dict(self.jira.put(endpoint, data=payload), endpoint)

    # ------------------------------------------------------------------
    # Custom field context default values
    # ------------------------------------------------------------------

    def get_field_context_defaults(
        self, field_id: str, context_ids: list[str] | None = None
    ) -> list[dict[str, Any]]:
        """Get default values grouped by custom field context.

        Args:
            field_id: Custom field ID.
            context_ids: Optional context IDs to filter by.

        Returns:
            A list of context default-value objects. Contexts without a
            configured default may not be present in the response.
        """
        field = _require_id("field_id", field_id)
        params: dict[str, Any] | None = None
        if context_ids:
            params = {
                "contextId": [
                    _require_id("context_id", context) for context in context_ids
                ]
            }
        endpoint = f"rest/api/3/field/{field}/context/defaultValues"
        return self._get_all_pages(endpoint, params=params)

    def set_field_context_default(
        self,
        field_id: str,
        context_id: str,
        default_value: dict[str, Any] | None,
        *,
        issue_type_id: str | None = None,
    ) -> None:
        """Set or remove a custom field context default value.

        Only the supplied context default target is changed. Passing
        ``None`` as ``default_value`` removes the default by sending
        ``value: null``. If ``issue_type_id`` is supplied, the target is the
        default for that issue type; otherwise it is the context catch-all.

        Args:
            field_id: Custom field ID.
            context_id: Field context ID.
            default_value: Default value object matching the custom field
                type, or ``None`` to remove the default.
            issue_type_id: Optional issue type ID. If omitted, update the
                context catch-all default.

        Raises:
            ValueError: If ``default_value`` is not an object or its type is
                missing, or if an identifier is invalid.
        """
        field = _require_id("field_id", field_id)
        context = _require_id("context_id", context_id)
        if default_value is not None and not isinstance(default_value, dict):
            raise ValueError("default_value must be an object or null")
        if isinstance(default_value, dict) and not default_value.get("type"):
            raise ValueError("default_value must contain a 'type' key")
        if not context.isdigit():
            raise ValueError("context_id must be numeric")

        target: dict[str, Any] = {"contextId": int(context)}
        if issue_type_id is None:
            target["isAnyIssueType"] = True
        else:
            target["issueTypeId"] = _require_id("issue_type_id", issue_type_id)
        target["value"] = default_value
        payload = {"defaultValues": [target]}
        endpoint = f"rest/api/3/field/{field}/context/defaultValues"
        self.jira.put(endpoint, data=payload)

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
