"""Jira dashboard and gadget operations.

Dashboard tools cover creating dashboards, adding/updating gadgets and
storing gadget configuration. Note that some legacy gadgets predate the
REST API and keep their configuration where it cannot be set via REST;
gadgets whose configuration is managed through dashboard item properties
(the common case, e.g. Filter Results) work with these tools.
"""

import logging
from copy import copy
from typing import Any

from requests.adapters import HTTPAdapter
from requests.exceptions import HTTPError, RequestException
from urllib3.util.retry import Retry

from mcp_atlassian.exceptions import MCPAtlassianAuthenticationError

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

    def change_dashboard_owner(
        self,
        dashboard_id: str,
        new_owner_account_id: str,
        *,
        use_admin_permissions: bool = False,
        autofix_name: bool = False,
        expected_owner_account_id: str | None = None,
    ) -> dict[str, Any]:
        """Change a Cloud dashboard owner once and verify the observed owner.

        Args:
            dashboard_id: Numeric dashboard ID.
            new_owner_account_id: Target Atlassian account ID.
            use_admin_permissions: Explicitly use Administer Jira permission.
            autofix_name: Allow Jira to rename a duplicate dashboard name.
            expected_owner_account_id: Optional preflight owner assertion. This
                is not an atomic concurrency guard.

        Returns:
            A confirmed, rejected, or indeterminate outcome, a stable local code,
            and observed owner IDs. ``changed`` is null when indeterminate; no
            automatic write retries or flag escalation are performed.
        """
        result: dict[str, Any] = {
            "success": False,
            "outcome": "rejected",
            "changed": False,
            "dashboard_id": dashboard_id,
            "requested_owner_account_id": new_owner_account_id,
            "previous_owner_account_id": None,
            "observed_owner_account_id": None,
        }

        def finish(outcome: str, code: str, message: str) -> dict[str, Any]:
            result.update(outcome=outcome, code=code, message=message)
            result["success"] = outcome == "confirmed"
            result["changed"] = None if outcome == "indeterminate" else False
            return result

        if not self.config.is_cloud:
            return finish(
                "rejected",
                "cloud_only",
                "Dashboard ownership changes support Jira Cloud only, not Server/DC.",
            )
        if (
            not isinstance(dashboard_id, str)
            or not dashboard_id.isascii()
            or not dashboard_id.isdecimal()
            or len(dashboard_id) > 19
            or not 0 < int(dashboard_id) <= 2**63 - 1
            or not isinstance(new_owner_account_id, str)
            or not new_owner_account_id.strip()
            or new_owner_account_id != new_owner_account_id.strip()
            or not isinstance(use_admin_permissions, bool)
            or not isinstance(autofix_name, bool)
            or (
                expected_owner_account_id is not None
                and (
                    not isinstance(expected_owner_account_id, str)
                    or not expected_owner_account_id.strip()
                    or expected_owner_account_id != expected_owner_account_id.strip()
                )
            )
        ):
            return finish("rejected", "invalid_input", "Invalid ownership parameters.")
        did = str(int(dashboard_id))
        result["dashboard_id"] = did

        def owner_of(dashboard: dict[str, Any]) -> str | None:
            owner = dashboard.get("owner")
            account_id = owner.get("accountId") if isinstance(owner, dict) else None
            return account_id if isinstance(account_id, str) and account_id else None

        try:
            previous = owner_of(self.get_dashboard(did))
        except (
            RequestException,
            MCPAtlassianAuthenticationError,
            ValueError,
            TypeError,
            RuntimeError,
        ):
            # The client can raise transport, authentication, or decoding errors.
            # Never expose exception text, URLs, or Jira response bodies.
            return finish(
                "rejected",
                "dashboard_read_failed",
                "Could not read the dashboard; no ownership write was attempted.",
            )
        result["previous_owner_account_id"] = previous
        result["observed_owner_account_id"] = previous
        if previous is None:
            return finish(
                "rejected",
                "owner_unavailable",
                "The dashboard owner account ID is unavailable; no write attempted.",
            )
        if (
            expected_owner_account_id is not None
            and previous != expected_owner_account_id
        ):
            return finish(
                "rejected",
                "owner_precondition_failed",
                "Observed owner does not match the expected owner; no write attempted.",
            )
        if previous == new_owner_account_id:
            return finish(
                "confirmed", "already_owner", "The requested owner is confirmed."
            )

        # Borrow connection pools/authentication, but isolate retry policy from
        # the shared client and session. Do not close these borrowed pools.
        client = copy(self.jira)
        client.retry_with_header = False
        client.backoff_and_retry = False
        client._session = copy(self.jira._session)
        client._session.cookies = copy(self.jira._session.cookies)
        client._session.adapters = dict(self.jira._session.adapters)
        for prefix, adapter in client._session.adapters.items():
            if not isinstance(adapter, HTTPAdapter):
                return finish(
                    "rejected",
                    "unsupported_transport",
                    "Cannot disable transport retries; no ownership write attempted.",
                )
            # HTTPAdapter's pickle hooks rebuild pools during copy(), so
            # explicitly restore the borrowed pools without closing them.
            local_adapter = copy(adapter)
            local_adapter.poolmanager = adapter.poolmanager
            local_adapter.proxy_manager = adapter.proxy_manager
            local_adapter.max_retries = Retry(total=0, redirect=0)
            client._session.adapters[prefix] = local_adapter

        rejected = False
        try:
            raw_response = client.request(
                "PUT",
                "rest/api/3/dashboard/bulk/edit",
                data={
                    "action": "changeOwner",
                    "entityIds": [int(did)],
                    "changeOwnerDetails": {
                        "newOwner": new_owner_account_id,
                        "autofixName": autofix_name,
                    },
                    "extendAdminPermissions": use_admin_permissions,
                },
                allow_redirects=False,
            )
            if raw_response.is_redirect or raw_response.is_permanent_redirect:
                raise HTTPError(response=raw_response)
            response = raw_response.json()
            # A 200 response can contain a per-dashboard rejection. Do not infer
            # causes (duplicate name, inactive user, permissions) from prose.
            errors = (
                response.get("entityErrors") if isinstance(response, dict) else None
            )
            rejected = isinstance(errors, dict) and bool(errors.get(did))
            if rejected:
                result["http_status"] = 200
        except HTTPError as exc:
            status = exc.response.status_code if exc.response is not None else None
            if isinstance(status, int):
                result["http_status"] = status
                rejected = 400 <= status < 500 and status not in (408, 429)
        except (
            RequestException,
            MCPAtlassianAuthenticationError,
            ValueError,
            TypeError,
            RuntimeError,
        ):
            # A lost response is not evidence that the write did not happen.
            logger.warning(
                "Dashboard ownership write response unavailable; verifying once"
            )

        # Exactly one verification read even if the write raised or returned
        # entityErrors. Never repeat a write or change caller-selected flags.
        result["observed_owner_account_id"] = None
        try:
            raw_response = client.request(
                "GET", f"rest/api/3/dashboard/{did}", allow_redirects=False
            )
            if raw_response.is_redirect or raw_response.is_permanent_redirect:
                raise HTTPError(response=raw_response)
            dashboard = raw_response.json()
            if not isinstance(dashboard, dict):
                raise TypeError("Unexpected dashboard response type")
            observed = owner_of(dashboard)
        except (
            RequestException,
            MCPAtlassianAuthenticationError,
            ValueError,
            TypeError,
            RuntimeError,
        ):
            return finish(
                "indeterminate",
                "verification_read_failed",
                "Ownership write attempted, but verification failed. Do not retry blindly.",
            )
        result["observed_owner_account_id"] = observed
        if observed == new_owner_account_id:
            finish("confirmed", "owner_confirmed", "The requested owner is confirmed.")
            result["changed"] = True
            return result
        if rejected and observed == previous:
            return finish(
                "rejected",
                "jira_rejected",
                "Jira rejected the ownership request; the previous owner was observed.",
            )
        return finish(
            "indeterminate",
            "owner_not_confirmed",
            "Requested owner not confirmed after the write attempt. Do not retry blindly.",
        )

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
