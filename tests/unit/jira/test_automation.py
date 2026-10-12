"""Tests for AutomationMixin (automation.py) and its server tools."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mcp_atlassian.exceptions import MCPAtlassianAuthenticationError
from mcp_atlassian.jira import JiraFetcher


@pytest.fixture
def mixin(mock_config, mock_atlassian_jira):
    with patch("atlassian.Jira") as cls:
        cls.return_value = mock_atlassian_jira
        fetcher = JiraFetcher(config=mock_config)
        fetcher.jira = mock_atlassian_jira
        yield fetcher


def _summary(values, next_link=None):
    links = {"self": "?limit=2"}
    if next_link:
        links["next"] = next_link
    return {"links": links, "data": values}


class TestAutomationRules:
    def test_cloud_id_is_resolved_once_and_cached(self, mixin):
        mixin.jira.get.side_effect = [{"cloudId": "abc-123"}]
        assert mixin._automation_cloud_id() == "abc-123"
        assert mixin._automation_cloud_id() == "abc-123"
        mixin.jira.get.assert_called_once_with("_edge/tenant_info")

    def test_oauth_sessions_are_rejected(self, mock_atlassian_jira):
        from mcp_atlassian.jira.config import JiraConfig
        from mcp_atlassian.utils.oauth import OAuthConfig

        with (
            patch("atlassian.Jira") as cls,
            patch(
                "mcp_atlassian.jira.client.configure_oauth_session", return_value=True
            ),
        ):
            cls.return_value = mock_atlassian_jira
            config = JiraConfig(
                url="https://example.atlassian.net",
                auth_type="oauth",
                oauth_config=OAuthConfig(
                    client_id="id",
                    client_secret="secret",  # noqa: S106
                    redirect_uri="https://example.atlassian.net/callback",
                    scope="read:jira-work",
                    access_token="token",  # noqa: S106
                    cloud_id="abc",
                    refresh_token="refresh",  # noqa: S106
                ),
            )
            fetcher = JiraFetcher(config=config)
            fetcher.jira = mock_atlassian_jira
            with pytest.raises(
                MCPAtlassianAuthenticationError, match="not available for OAuth"
            ):
                fetcher._automation_cloud_id()

    def test_list_without_filters_uses_get(self, mixin):
        mixin._cloud_id = "abc-123"
        mixin.jira.get.return_value = _summary([{"name": "Rule one"}])

        result = mixin.list_automation_rules(limit=2)

        assert result["data"][0]["name"] == "Rule one"
        mixin.jira.get.assert_called_once_with(
            "https://api.atlassian.com/automation/public/jira/abc-123/rest/v1/rule/summary",
            params={"limit": 2},
            absolute=True,
        )

    def test_list_with_filter_uses_post(self, mixin):
        mixin._cloud_id = "abc-123"
        mixin.jira.post.return_value = _summary(
            [], next_link="?cursor=next&limit=2"
        )

        result = mixin.list_automation_rules(limit=2, state="ENABLED", cursor="cur")

        assert result["links"]["next"] == "?cursor=next&limit=2"
        mixin.jira.post.assert_called_once_with(
            "https://api.atlassian.com/automation/public/jira/abc-123/rest/v1/rule/summary",
            json={"limit": 2, "cursor": "cur", "state": "ENABLED"},
            absolute=True,
        )
        mixin.jira.get.assert_not_called()

    def test_get_rule_by_uuid(self, mixin):
        mixin._cloud_id = "abc-123"
        mixin.jira.get.return_value = {"rule": {"name": "Rule one"}}

        assert mixin.get_automation_rule("0192e5ac-0e25") == {
            "rule": {"name": "Rule one"}
        }
        mixin.jira.get.assert_called_once_with(
            "https://api.atlassian.com/automation/public/jira/abc-123/rest/v1/rule/0192e5ac-0e25",
            absolute=True,
        )

    def test_create_rule_sends_rule_and_connections(self, mixin):
        mixin._cloud_id = "abc-123"
        mixin.jira.post.return_value = {"ruleUuid": "0192e5ac"}

        result = mixin.create_automation_rule(
            {"name": "Rule one"}, connections=[{"id": "c1"}]
        )

        assert result == {"ruleUuid": "0192e5ac"}
        mixin.jira.post.assert_called_once_with(
            "https://api.atlassian.com/automation/public/jira/abc-123/rest/v1/rule",
            json={"rule": {"name": "Rule one"}, "connections": [{"id": "c1"}]},
            absolute=True,
        )

    def test_update_rule_uses_put(self, mixin):
        mixin._cloud_id = "abc-123"
        mixin.jira.put.return_value = {"ruleUuid": "0192e5ac"}

        result = mixin.update_automation_rule("0192e5ac", {"name": "Rule one"})

        assert result == {"ruleUuid": "0192e5ac"}
        mixin.jira.put.assert_called_once_with(
            "https://api.atlassian.com/automation/public/jira/abc-123/rest/v1/rule/0192e5ac",
            data={"rule": {"name": "Rule one"}, "connections": []},
            absolute=True,
        )

    def test_set_state_validates_input(self, mixin):
        with pytest.raises(ValueError, match="ENABLED or DISABLED"):
            mixin.set_automation_rule_state("0192e5ac", "PAUSED")
        mixin.jira.put.assert_not_called()

    def test_set_state_enables_rule(self, mixin):
        mixin._cloud_id = "abc-123"
        mixin.jira.put.return_value = {"ruleUuid": "0192e5ac"}

        mixin.set_automation_rule_state("0192e5ac", "ENABLED")

        mixin.jira.put.assert_called_once_with(
            "https://api.atlassian.com/automation/public/jira/abc-123/rest/v1/rule/0192e5ac/state",
            data={"value": "ENABLED"},
            absolute=True,
        )


WRITE_TOOLS = [
    ("create_automation_rule", {"rule": {"name": "n"}}),
    ("update_automation_rule", {"rule_uuid": "u", "rule": {"name": "n"}}),
    ("set_automation_rule_state", {"rule_uuid": "u", "state": "ENABLED"}),
]


def _ctx(read_only):
    ctx = MagicMock()
    ctx.request_context.lifespan_context = {
        "app_lifespan_context": MagicMock(read_only=read_only)
    }
    return ctx


@pytest.mark.anyio
class TestServerTools:
    @pytest.mark.parametrize(("tool", "kwargs"), WRITE_TOOLS)
    async def test_write_tools_blocked_in_read_only_mode(self, tool, kwargs):
        from mcp_atlassian.servers import jira as server

        fetcher_getter = AsyncMock()
        with patch.object(server, "get_jira_fetcher", fetcher_getter):
            fn = getattr(server, tool).fn
            with pytest.raises(ValueError, match="read-only mode"):
                await fn(_ctx(read_only=True), **kwargs)
        fetcher_getter.assert_not_called()

    async def test_list_rules_reports_oauth_error(self):
        from mcp_atlassian.servers import jira as server

        fetcher = MagicMock()
        fetcher.list_automation_rules.side_effect = (
            MCPAtlassianAuthenticationError("not available for OAuth")
        )
        with patch.object(server, "get_jira_fetcher", AsyncMock(return_value=fetcher)):
            out = await server.list_automation_rules.fn(MagicMock())
        data = json.loads(out)
        assert data["success"] is False
        assert "not available for OAuth" in data["error"]

    async def test_list_rules_returns_data_and_links(self):
        from mcp_atlassian.servers import jira as server

        fetcher = MagicMock()
        fetcher.list_automation_rules.return_value = {
            "data": [{"name": "Rule one", "state": "ENABLED"}],
            "links": {"next": "?cursor=next"},
        }
        with patch.object(server, "get_jira_fetcher", AsyncMock(return_value=fetcher)):
            out = await server.list_automation_rules.fn(MagicMock())
        data = json.loads(out)
        assert data["success"] is True
        assert data["rules"][0]["state"] == "ENABLED"
        assert data["links"]["next"] == "?cursor=next"
        fetcher.list_automation_rules.assert_called_once_with(
            limit=50, cursor=None, trigger=None, state=None, scope=None
        )
