"""Module for Jira issue security scheme operations."""

import logging
from typing import Any

from requests.exceptions import HTTPError

from ..exceptions import MCPAtlassianAuthenticationError
from .client import JiraClient

logger = logging.getLogger("mcp-jira")


class SecurityMixin(JiraClient):
    """Mixin for Jira issue security scheme operations."""

    def get_security_level_members(
        self, level_id: str | int
    ) -> dict[str, Any]:
        """
        Get members (groups, users, roles) that have access to a security level.

        Uses the Jira Cloud REST API v3 endpoint:
            GET /rest/api/3/issuesecurityschemes/level/member?levelId={levelId}

        Args:
            level_id: The numeric ID of the security level.

        Returns:
            Dict with ``success``, ``level_id``, ``total``, and ``members`` keys.
            Each member has ``type``, ``parameter``, and optionally ``value``.

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
            if http_err.response is not None and http_err.response.status_code in (401, 403):
                raise MCPAtlassianAuthenticationError(
                    f"Authentication/permission error fetching security level members: {http_err}"
                ) from http_err
            raise
        except Exception as e:
            logger.error(f"Error fetching security level members for level {level_id}: {e}")
            raise
