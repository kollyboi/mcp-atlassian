"""Tests for DashboardsMixin (dashboards.py) and its server tools."""

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


FILTER_RESULTS_URI = (
    "rest/gadgets/1.0/g/com.atlassian.jira.gadgets:filter-results-gadget"
    "/gadgets/filter-results-gadget.xml"
)


class TestDashboardReads:
    def test_search_dashboards(self, mixin):
        mixin.jira.get.return_value = {
            "values": [{"id": 10092, "name": "BB BT5"}],
            "total": 1,
            "isLast": True,
        }
        mixin.search_dashboards("BB BT5", start_at=0, max_results=20)
        mixin.jira.get.assert_called_once_with(
            "rest/api/3/dashboard/search",
            params={
                "startAt": 0,
                "maxResults": 20,
                "dashboardNameFilter": "BB BT5",
            },
        )

    def test_get_dashboard_path(self, mixin):
        mixin.jira.get.return_value = {"id": 10092, "name": "BB BT5"}
        assert mixin.get_dashboard("10092")["name"] == "BB BT5"
        mixin.jira.get.assert_called_once_with("rest/api/3/dashboard/10092")

    def test_get_dashboard_gadgets_unwraps_gadgets(self, mixin):
        mixin.jira.get.return_value = {
            "gadgets": [{"id": 13023, "title": "Filter Results"}]
        }
        assert mixin.get_dashboard_gadgets("10092")[0]["id"] == 13023
        mixin.jira.get.assert_called_once_with("rest/api/3/dashboard/10092/gadget")

    def test_get_available_gadgets(self, mixin):
        mixin.jira.get.return_value = {"gadgets": [{"uri": FILTER_RESULTS_URI}]}
        assert mixin.get_available_dashboard_gadgets()[0]["uri"] == FILTER_RESULTS_URI
        mixin.jira.get.assert_called_once_with("rest/api/3/dashboard/gadgets")

    def test_get_gadget_config_returns_all_properties(self, mixin):
        def get(url):
            if url == "rest/api/3/dashboard/10092/items/13023/properties":
                return {"keys": [{"key": "config"}]}
            if url == "rest/api/3/dashboard/10092/items/13023/properties/config":
                return {"key": "config", "value": {"filterId": "10471"}}
            raise AssertionError(f"unexpected GET {url}")

        mixin.jira.get.side_effect = get
        assert mixin.get_gadget_config("10092", "13023") == {
            "config": {"filterId": "10471"}
        }


class TestDashboardWrites:
    def test_create_dashboard_defaults_to_private(self, mixin):
        mixin.jira.post.return_value = {"id": 10100, "name": "Fraviksoversikt"}
        result = mixin.create_dashboard("Fraviksoversikt")
        assert result["id"] == 10100
        mixin.jira.post.assert_called_once_with(
            "rest/api/3/dashboard",
            json={
                "name": "Fraviksoversikt",
                "sharePermissions": [],
                "editPermissions": [],
            },
        )

    def test_add_gadget_with_uri_and_position(self, mixin):
        mixin.jira.post.return_value = {"id": 13030, "title": "Filter Results"}
        result = mixin.add_gadget("10092", uri=FILTER_RESULTS_URI, column=0, row=0)
        assert result["id"] == 13030
        mixin.jira.post.assert_called_once_with(
            "rest/api/3/dashboard/10092/gadget",
            json={
                "ignoreUriAndModuleKeyValidation": False,
                "uri": FILTER_RESULTS_URI,
                "position": {"column": 0, "row": 0},
            },
        )

    def test_add_gadget_requires_exactly_one_identifier(self, mixin):
        with pytest.raises(ValueError, match="exactly one"):
            mixin.add_gadget("10092")
        with pytest.raises(ValueError, match="exactly one"):
            mixin.add_gadget("10092", uri="u", module_key="m")
        mixin.jira.post.assert_not_called()

    def test_add_gadget_validates_color(self, mixin):
        with pytest.raises(ValueError, match="Invalid gadget color"):
            mixin.add_gadget("10092", uri=FILTER_RESULTS_URI, color="pink")
        mixin.jira.post.assert_not_called()

    def test_update_gadget_payload(self, mixin):
        mixin.jira.put.return_value = None
        mixin.update_gadget("10092", "13023", title="Fravik", color="blue", row=1)
        mixin.jira.put.assert_called_once_with(
            "rest/api/3/dashboard/10092/gadget/13023",
            data={"title": "Fravik", "color": "blue", "position": {"row": 1}},
        )

    def test_update_gadget_requires_a_change(self, mixin):
        with pytest.raises(ValueError, match="at least one"):
            mixin.update_gadget("10092", "13023")
        mixin.jira.put.assert_not_called()

    def test_set_gadget_config_default_key(self, mixin):
        mixin.jira.put.return_value = None
        mixin.set_gadget_config(
            "10092", "13023", {"filterId": "10471", "isConfigured": "true"}
        )
        mixin.jira.put.assert_called_once_with(
            "rest/api/3/dashboard/10092/items/13023/properties/config",
            data={"filterId": "10471", "isConfigured": "true"},
        )

    def test_set_gadget_config_rejects_empty_config(self, mixin):
        with pytest.raises(ValueError, match="non-empty"):
            mixin.set_gadget_config("10092", "13023", {})
        mixin.jira.put.assert_not_called()


WRITE_TOOLS = [
    ("create_dashboard", {"name": "n"}),
    ("add_gadget", {"dashboard_id": "1", "uri": "u"}),
    ("update_gadget", {"dashboard_id": "1", "gadget_id": "2", "title": "t"}),
    (
        "set_gadget_config",
        {"dashboard_id": "1", "item_id": "2", "config": {"k": "v"}},
    ),
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

    async def test_search_dashboards_unpacks_dashboards(self):
        from mcp_atlassian.servers import jira as server

        fetcher = MagicMock()
        fetcher.search_dashboards.return_value = {
            "values": [{"id": 10092, "name": "BB BT5"}],
            "total": 1,
            "startAt": 0,
            "maxResults": 50,
            "isLast": True,
        }
        with patch.object(server, "get_jira_fetcher", AsyncMock(return_value=fetcher)):
            out = await server.search_dashboards.fn(MagicMock(), filter="BB BT5")
        data = json.loads(out)
        assert data["success"] is True
        assert data["dashboards"][0]["id"] == 10092
        fetcher.search_dashboards.assert_called_once_with(
            "BB BT5", start_at=0, max_results=50
        )
