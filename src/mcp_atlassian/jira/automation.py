"""Jira Automation rule management via the Automation REST API.

The Automation REST API lives on ``api.atlassian.com`` instead of the site
URL, so calls are made with absolute URLs built from the site's cloud ID.
Atlassian documents this API for API tokens and browser sessions; OAuth2
apps are blocked, so OAuth sessions get a clear error instead of a 403.
"""

import logging
from typing import Any

from mcp_atlassian.exceptions import MCPAtlassianAuthenticationError

from .client import JiraClient
from .screens import _require_id

logger = logging.getLogger("mcp-jira")

_AUTOMATION_URL = "https://api.atlassian.com/automation/public/jira"


class AutomationMixin(JiraClient):
    """Mixin for Jira Cloud automation rule management."""

    _cloud_id: str | None

    def _automation_cloud_id(self) -> str:
        """Return the site's cloud ID, caching the lookup.

        Returns:
            The cloud ID of the configured Jira site.

        Raises:
            MCPAtlassianAuthenticationError: If authenticated with OAuth,
                which Atlassian does not allow for this API.
        """
        cached = getattr(self, "_cloud_id", None)
        if cached:
            return cached
        if self.config.auth_type == "oauth":
            msg = (
                "The Automation REST API is not available for OAuth "
                "authentication; Atlassian requires an API token or a "
                "browser session."
            )
            raise MCPAtlassianAuthenticationError(msg)
        response = self.jira.get("_edge/tenant_info")
        if not isinstance(response, dict) or not response.get("cloudId"):
            msg = f"Could not resolve the cloud ID from _edge/tenant_info: {response!r}"
            raise TypeError(msg)
        self._cloud_id = str(response["cloudId"])
        return self._cloud_id

    def _automation_url(self, path: str) -> str:
        """Build an absolute Automation API URL for a path-relative endpoint."""
        return f"{_AUTOMATION_URL}/{self._automation_cloud_id()}/rest/v1/{path}"

    def list_automation_rules(
        self,
        *,
        limit: int = 50,
        cursor: str | None = None,
        trigger: str | None = None,
        state: str | None = None,
        scope: str | None = None,
        author: str | None = None,
    ) -> dict[str, Any]:
        """List automation rule summaries.

        Without filters this uses the plain summary endpoint; with any of
        ``trigger``, ``state``, ``scope`` or ``author`` it uses the POST
        search endpoint, which is the only way to filter.

        Args:
            limit: Page size.
            cursor: Opaque cursor from a previous response's ``links``.
            trigger: Filter by trigger, e.g.
                ``jira.version.event.trigger:created``.
            state: Filter by state, ``ENABLED`` or ``DISABLED``.
            scope: Filter by a single ARI, e.g.
                ``ari:cloud:jira::site/<cloud-id>`` or a project ARI.
            author: Filter by author account ID.

        Returns:
            The rule summary response with ``data`` (rule summaries) and
            ``links`` (next cursor, if more rules exist).
        """
        params: dict[str, Any] = {"limit": limit}
        if cursor:
            params["cursor"] = cursor
        has_filters = any((trigger, state, scope, author))
        if has_filters:
            params.update(
                {
                    "trigger": trigger,
                    "state": state,
                    "scope": scope,
                    "author": author,
                }
            )
            params = {k: v for k, v in params.items() if v is not None}
            response = self.jira.post(
                self._automation_url("rule/summary"),
                json=params,
                absolute=True,
            )
        else:
            response = self.jira.get(
                self._automation_url("rule/summary"),
                params=params,
                absolute=True,
            )
        if not isinstance(response, dict):
            msg = f"Unexpected response type from automation summary: {type(response)}"
            raise TypeError(msg)
        return response

    def get_automation_rule(self, rule_uuid: str) -> dict[str, Any]:
        """Get a full automation rule payload by UUID.

        Args:
            rule_uuid: The rule's UUID.

        Returns:
            The rule config, including its trigger and components.
        """
        uuid = _require_id("rule_uuid", rule_uuid)
        return self._expect_automation_dict(
            self.jira.get(self._automation_url(f"rule/{uuid}"), absolute=True),
            f"rule/{uuid}",
        )

    def create_automation_rule(
        self,
        rule: dict[str, Any],
        connections: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Create an automation rule.

        The rule payload has the same structure as the response of
        ``get_automation_rule`` (name, trigger, components, state,
        ruleScopeARIs, ...).

        Args:
            rule: The rule payload.
            connections: Optional connection definitions the rule needs.

        Returns:
            The created rule's UUID as ``{"ruleUuid": ...}``.
        """
        payload: dict[str, Any] = {"rule": rule, "connections": connections or []}
        logger.info(f"Creating automation rule '{rule.get('name', '<unnamed>')}'")
        return self._expect_automation_dict(
            self.jira.post(self._automation_url("rule"), json=payload, absolute=True),
            "rule",
        )

    def update_automation_rule(
        self,
        rule_uuid: str,
        rule: dict[str, Any],
        connections: list[dict[str, Any]] | None = None,
    ) -> dict[str, Any]:
        """Update an existing automation rule.

        The payload has the same structure as ``get_automation_rule``'s
        response; component IDs are only required for pre-existing
        components.

        Args:
            rule_uuid: UUID of the rule to update.
            rule: The complete rule payload.
            connections: Optional connection definitions the rule needs.

        Returns:
            The updated rule's UUID as ``{"ruleUuid": ...}``.
        """
        uuid = _require_id("rule_uuid", rule_uuid)
        payload: dict[str, Any] = {"rule": rule, "connections": connections or []}
        logger.info(f"Updating automation rule {uuid}")
        return self._expect_automation_dict(
            self.jira.put(
                self._automation_url(f"rule/{uuid}"), data=payload, absolute=True
            ),
            f"rule/{uuid}",
        )

    def set_automation_rule_state(self, rule_uuid: str, state: str) -> dict[str, Any]:
        """Enable or disable an automation rule.

        Args:
            rule_uuid: UUID of the rule.
            state: ``ENABLED`` or ``DISABLED``.

        Returns:
            The response, e.g. ``{"ruleUuid": ...}``.
        """
        if state not in ("ENABLED", "DISABLED"):
            raise ValueError("state must be ENABLED or DISABLED")
        uuid = _require_id("rule_uuid", rule_uuid)
        logger.info(f"Setting automation rule {uuid} to {state}")
        return self._expect_automation_dict(
            self.jira.put(
                self._automation_url(f"rule/{uuid}/state"),
                data={"value": state},
                absolute=True,
            ),
            f"rule/{uuid}/state",
        )

    def _expect_automation_dict(self, response: Any, endpoint: str) -> dict[str, Any]:
        if not isinstance(response, dict):
            msg = (
                f"Unexpected response type from automation {endpoint}: {type(response)}"
            )
            raise TypeError(msg)
        return response
