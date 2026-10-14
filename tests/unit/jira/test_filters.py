"""Tests for FiltersMixin (filters.py) and its server tools."""

import json
from unittest.mock import AsyncMock, MagicMock, patch

import pytest

from mcp_atlassian.jira import JiraFetcher


@pytest.fixture
def mixin(mock_config, mock_atlassian_jira):
    with patch("atlassian.Jira") as cls:
        cls.return_value = mock_atlassian_jira
        fetcher = JiraFetcher(config=mock_config)
        fetcher.jira = mock_atlassian_jira
        yield fetcher


class TestFilterReads:
    def test_search_by_prefix_by_default(self, mixin):
        mixin.jira.get.return_value = {"values": [], "total": 0, "isLast": True}

        mixin.search_filters("BB5")

        mixin.jira.get.assert_called_once_with(
            "rest/api/3/filter/search",
            params={
                "startAt": 0,
                "maxResults": 50,
                "filterName": "BB5",
            },
        )

    def test_search_with_substring_match_and_owner(self, mixin):
        mixin.jira.get.return_value = {"values": [], "total": 0, "isLast": True}

        mixin.search_filters("BB5", substring_match=True, owner="someone@example.com")

        mixin.jira.get.assert_called_once_with(
            "rest/api/3/filter/search",
            params={
                "startAt": 0,
                "maxResults": 50,
                "filterName": "BB5",
                "isSubstringMatch": "true",
                "owner": "someone@example.com",
            },
        )

    def test_get_filter_path(self, mixin):
        mixin.jira.get.return_value = {"id": "10471", "name": "Avvik", "jql": "x"}
        assert mixin.get_filter("10471")["jql"] == "x"
        mixin.jira.get.assert_called_once_with("rest/api/3/filter/10471")

    def test_get_filter_columns(self, mixin):
        mixin.jira.get.return_value = [{"label": "Key", "value": "issuekey"}]
        assert mixin.get_filter_columns("10471")[0]["value"] == "issuekey"
        mixin.jira.get.assert_called_once_with("rest/api/3/filter/10471/columns")

    def test_get_filter_columns_without_layout_returns_empty(self, mixin):
        from requests.exceptions import HTTPError

        response = MagicMock(status_code=404)
        mixin.jira.get.side_effect = HTTPError(
            "Column layout does not exist for filter", response=response
        )
        assert mixin.get_filter_columns("10471") == []

    def test_get_filter_columns_other_errors_propagate(self, mixin):
        from requests.exceptions import HTTPError

        response = MagicMock(status_code=403)
        mixin.jira.get.side_effect = HTTPError("Forbidden", response=response)
        with pytest.raises(HTTPError):
            mixin.get_filter_columns("10471")


class TestFilterWrites:
    def test_create_filter_payload(self, mixin):
        mixin.jira.post.return_value = {"id": "10500", "name": "Avvik"}
        result = mixin.create_filter(
            "Avvik", "project = BB5", description="d", favourite=True
        )
        assert result["id"] == "10500"
        mixin.jira.post.assert_called_once_with(
            "rest/api/3/filter",
            json={
                "name": "Avvik",
                "jql": "project = BB5",
                "description": "d",
                "favourite": True,
            },
        )

    def test_update_filter_is_a_full_replacement(self, mixin):
        mixin.jira.put.return_value = {"id": "10471", "name": "Avvik"}
        result = mixin.update_filter("10471", "Avvik", "project = BB5")
        assert result["id"] == "10471"
        mixin.jira.put.assert_called_once_with(
            "rest/api/3/filter/10471",
            data={"name": "Avvik", "jql": "project = BB5"},
        )

    def test_set_filter_columns(self, mixin):
        mixin.jira.put.return_value = None
        mixin.set_filter_columns("10471", ["issuetype", "issuekey", "summary"])
        mixin.jira.put.assert_called_once_with(
            "rest/api/3/filter/10471/columns",
            data={"columns": ["issuetype", "issuekey", "summary"]},
        )

    def test_set_filter_columns_requires_columns(self, mixin):
        with pytest.raises(ValueError, match="at least one"):
            mixin.set_filter_columns("10471", [])
        mixin.jira.put.assert_not_called()

    def test_add_share_permission_payload(self, mixin):
        mixin.jira.post.return_value = {"type": "group", "groupname": "jira-admins"}
        result = mixin.add_filter_share_permission(
            "10471", "group", group_name="jira-admins", rights=1
        )
        assert result["type"] == "group"
        mixin.jira.post.assert_called_once_with(
            "rest/api/3/filter/10471/permission",
            json={"type": "group", "groupname": "jira-admins", "rights": 1},
        )

    def test_add_share_permission_validates_type(self, mixin):
        with pytest.raises(ValueError, match="Invalid permission type"):
            mixin.add_filter_share_permission("10471", "everyone")
        mixin.jira.post.assert_not_called()


WRITE_TOOLS = [
    ("create_filter", {"name": "n", "jql": "project = X"}),
    ("update_filter", {"filter_id": "1", "name": "n", "jql": "project = X"}),
    ("set_filter_columns", {"filter_id": "1", "columns": "summary"}),
    ("add_filter_share_permission", {"filter_id": "1", "permission_type": "global"}),
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

    async def test_search_filters_unpacks_values(self):
        from mcp_atlassian.servers import jira as server

        fetcher = MagicMock()
        fetcher.search_filters.return_value = {
            "values": [{"id": "10471", "name": "Avvik"}],
            "total": 1,
            "startAt": 0,
            "maxResults": 50,
            "isLast": True,
        }
        with patch.object(server, "get_jira_fetcher", AsyncMock(return_value=fetcher)):
            out = await server.search_filters.fn(MagicMock(), filter_name="Avvik")
        data = json.loads(out)
        assert data["success"] is True
        assert data["filters"][0]["id"] == "10471"
        assert data["total"] == 1
        fetcher.search_filters.assert_called_once_with(
            "Avvik",
            substring_match=False,
            owner=None,
            order_by=None,
            start_at=0,
            max_results=50,
        )

    async def test_set_filter_columns_splits_csv(self):
        from mcp_atlassian.servers import jira as server

        fetcher = MagicMock()
        with patch.object(server, "get_jira_fetcher", AsyncMock(return_value=fetcher)):
            out = await server.set_filter_columns.fn(
                MagicMock(), filter_id="10471", columns="summary, status ,"
            )
        data = json.loads(out)
        assert data["success"] is True
        fetcher.set_filter_columns.assert_called_once_with(
            "10471", ["summary", "status"]
        )
