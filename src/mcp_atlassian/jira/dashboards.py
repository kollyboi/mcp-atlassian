"""Jira dashboard and gadget operations.

Dashboard tools cover creating dashboards, adding/updating gadgets and
storing gadget configuration. Note that some legacy gadgets predate the
REST API and keep their configuration where it cannot be set via REST;
gadgets whose configuration is managed through dashboard item properties
(the common case, e.g. Filter Results) work with these tools.
"""

import logging
from typing import Any

from .client import JiraClient
from .screens import _require_id

logger = logging.getLogger("mcp-jira")

_GADGET_COLORS = (
    "blue",
    "red",
    "yellow",
    "green",
    "cyan",
    "purple",
    "gray",
    "white",
)


class DashboardsMixin(JiraClient):
    """Mixin for Jira Cloud dashboard operations."""

    def search_dashboards(
        self,
        filter: str | None = None,
        start_at: int = 0,
        max_results: int = 50,
    ) -> dict[str, Any]:
        """Search dashboards by name substring.

        Args:
            filter: Dashboard name substring.
            start_at: Zero-based result offset.
            max_results: Maximum results.

        Returns:
            Jira's paginated response with ``values``, ``total``, ``isLast``
            and ``nextPage``.
        """
        params: dict[str, Any] = {"startAt": start_at, "maxResults": max_results}
        if filter:
            params["dashboardNameFilter"] = filter
        response = self.jira.get("rest/api/3/dashboard/search", params=params)
        if not isinstance(response, dict):
            msg = f"Unexpected response type from dashboard search: {type(response)}"
            raise TypeError(msg)
        return response

    def get_dashboard(self, dashboard_id: str) -> dict[str, Any]:
        """Get a dashboard's details.

        Args:
            dashboard_id: Numeric dashboard ID.

        Returns:
            The dashboard with name, description, owner and permissions.
        """
        did = _require_id("dashboard_id", dashboard_id)
        endpoint = f"rest/api/3/dashboard/{did}"
        response = self.jira.get(endpoint)
        if not isinstance(response, dict):
            msg = f"Unexpected response type from {endpoint}: {type(response)}"
            raise TypeError(msg)
        return response

    def get_dashboard_gadgets(self, dashboard_id: str) -> list[dict[str, Any]]:
        """Get the gadgets on a dashboard.

        Args:
            dashboard_id: Numeric dashboard ID.

        Returns:
            Gadget dicts with ``id``, ``moduleKey``/``uri``, ``title`` and
            ``position``. The gadget ``id`` is needed as ``item_id`` for
            gadget configuration properties.
        """
        did = _require_id("dashboard_id", dashboard_id)
        endpoint = f"rest/api/3/dashboard/{did}/gadget"
        response = self.jira.get(endpoint)
        if not isinstance(response, dict) or not isinstance(
            response.get("gadgets"), list
        ):
            msg = f"Unexpected response type from {endpoint}: {type(response)}"
            raise TypeError(msg)
        return response["gadgets"]

    def get_available_dashboard_gadgets(self) -> list[dict[str, Any]]:
        """List the gadget types that can be added to dashboards.

        Returns:
            Available gadgets with ``uri`` or ``moduleKey`` and ``title``.
        """
        response = self.jira.get("rest/api/3/dashboard/gadgets")
        if not isinstance(response, dict) or not isinstance(
            response.get("gadgets"), list
        ):
            msg = f"Unexpected response type from dashboard gadgets: {type(response)}"
            raise TypeError(msg)
        return response["gadgets"]

    def get_gadget_config(self, dashboard_id: str, item_id: str) -> dict[str, Any]:
        """Get all stored configuration properties of a dashboard gadget.

        Args:
            dashboard_id: Numeric dashboard ID.
            item_id: The gadget's ID (the ``id`` from get_dashboard_gadgets).

        Returns:
            Dict mapping each property key to its stored value. Filter
            Results gadgets store their settings under ``config``, with
            ``filterId``, ``columnNames``, ``num`` and similar string keys.
        """
        did = _require_id("dashboard_id", dashboard_id)
        item = _require_id("item_id", item_id)
        keys_response = self.jira.get(
            f"rest/api/3/dashboard/{did}/items/{item}/properties"
        )
        if not isinstance(keys_response, dict) or not isinstance(
            keys_response.get("keys"), list
        ):
            msg = "Unexpected response type from gadget properties: "
            f"{type(keys_response)}"
            raise TypeError(msg)
        config: dict[str, Any] = {}
        for entry in keys_response["keys"]:
            key = entry["key"]
            value = self.jira.get(
                f"rest/api/3/dashboard/{did}/items/{item}/properties/{key}"
            )
            if isinstance(value, dict) and value.get("key") == key:
                value = value.get("value")
            config[key] = value
        return config

    def create_dashboard(
        self,
        name: str,
        description: str | None = None,
        share_permissions: list[dict[str, Any]] | None = None,
        edit_permissions: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Create a dashboard owned by the current user.

        Args:
            name: Dashboard name.
            description: Optional dashboard description.
            share_permissions: Share permissions; empty (the default)
                creates a private dashboard.
            edit_permissions: Edit permissions; defaults to the same as
                the share permissions.

        Returns:
            The created dashboard, including its numeric ``id``.
        """
        payload: dict[str, Any] = {
            "name": name,
            "sharePermissions": share_permissions or [],
            "editPermissions": (
                edit_permissions if edit_permissions is not None else share_permissions
            )
            or [],
        }
        if description is not None:
            payload["description"] = description
        logger.info(f"Creating dashboard '{name}'")
        response = self.jira.post("rest/api/3/dashboard", json=payload)
        if not isinstance(response, dict):
            msg = f"Unexpected response type from dashboard create: {type(response)}"
            raise TypeError(msg)
        return response

    def add_gadget(
        self,
        dashboard_id: str,
        *,
        uri: str | None = None,
        module_key: str | None = None,
        title: str | None = None,
        color: str | None = None,
        column: int | None = None,
        row: int | None = None,
        ignore_validation: bool = False,
    ) -> dict[str, Any]:
        """Add a gadget to a dashboard.

        Args:
            dashboard_id: Numeric dashboard ID.
            uri: URI of the gadget type, e.g.
                ``rest/gadgets/1.0/g/com.atlassian.jira.gadgets:filter-results-gadget/gadgets/filter-results-gadget.xml``.
            module_key: Module key of the gadget type (alternative to
                ``uri``), e.g.
                ``com.atlassian.plugins.atlassian-connect-plugin:...``.
            title: Optional gadget title; defaults to the gadget type's.
            color: Optional gadget color (blue, red, yellow, green, cyan,
                purple, gray, white).
            column: Optional 0-based column position.
            row: Optional row position; other gadgets in the same column
                move down.
            ignore_validation: Skip URI/module key validation, for gadget
                types from applications that are not installed.

        Returns:
            The created gadget, including its numeric ``id``.

        Raises:
            ValueError: If neither ``uri`` nor ``module_key`` is given, or
                if both are.
        """
        did = _require_id("dashboard_id", dashboard_id)
        if bool(uri) == bool(module_key):
            raise ValueError("Provide exactly one of uri or module_key")
        payload: dict[str, Any] = {"ignoreUriAndModuleKeyValidation": ignore_validation}
        if uri:
            payload["uri"] = uri
        if module_key:
            payload["moduleKey"] = module_key
        if title is not None:
            payload["title"] = title
        if color is not None:
            if color not in _GADGET_COLORS:
                raise ValueError(f"Invalid gadget color: {color!r}")
            payload["color"] = color
        position: dict[str, int] = {}
        if column is not None:
            position["column"] = column
        if row is not None:
            position["row"] = row
        if position:
            payload["position"] = position
        endpoint = f"rest/api/3/dashboard/{did}/gadget"
        logger.info(f"Adding gadget to dashboard {did}")
        response = self.jira.post(endpoint, json=payload)
        if not isinstance(response, dict):
            msg = f"Unexpected response type from {endpoint}: {type(response)}"
            raise TypeError(msg)
        return response

    def update_gadget(
        self,
        dashboard_id: str,
        gadget_id: str,
        *,
        title: str | None = None,
        color: str | None = None,
        column: int | None = None,
        row: int | None = None,
    ) -> None:
        """Update a gadget's title, color and/or position.

        Args:
            dashboard_id: Numeric dashboard ID.
            gadget_id: The gadget's numeric ID.
            title: New title.
            color: New color (blue, red, yellow, green, cyan, purple, gray,
                white).
            column: New 0-based column position.
            row: New row position.

        Raises:
            ValueError: If no field to update is given.
        """
        did = _require_id("dashboard_id", dashboard_id)
        gid = _require_id("gadget_id", gadget_id)
        payload: dict[str, Any] = {}
        if title is not None:
            payload["title"] = title
        if color is not None:
            if color not in _GADGET_COLORS:
                raise ValueError(f"Invalid gadget color: {color!r}")
            payload["color"] = color
        position: dict[str, int] = {}
        if column is not None:
            position["column"] = column
        if row is not None:
            position["row"] = row
        if position:
            payload["position"] = position
        if not payload:
            raise ValueError("Provide at least one field to update")
        logger.info(f"Updating gadget {gid} on dashboard {did}")
        self.jira.put(f"rest/api/3/dashboard/{did}/gadget/{gid}", data=payload)

    def set_gadget_config(
        self,
        dashboard_id: str,
        item_id: str,
        config: dict[str, Any],
        property_key: str = "config",
    ) -> None:
        """Store a dashboard gadget's configuration.

        For the standard Jira gadgets (Filter Results, charts, ...) this
        goes under the ``config`` property with keys like ``filterId``,
        ``columnNames``, ``num`` and ``isConfigured``; Jira expects the
        values as strings. Inspect an existing gadget with
        ``get_gadget_config`` for the exact keys a gadget type uses.

        Args:
            dashboard_id: Numeric dashboard ID.
            item_id: The gadget's numeric ID.
            config: The configuration value to store.
            property_key: Property key, ``config`` for standard gadgets.
        """
        did = _require_id("dashboard_id", dashboard_id)
        item = _require_id("item_id", item_id)
        if not isinstance(config, dict) or not config:
            raise ValueError("config must be a non-empty object")
        key = _require_id("property_key", property_key)
        logger.info(f"Setting {key} for dashboard item {item} on {did}")
        self.jira.put(
            f"rest/api/3/dashboard/{did}/items/{item}/properties/{key}",
            data=config,
        )
