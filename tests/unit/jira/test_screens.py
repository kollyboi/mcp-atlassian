"""Tests for ScreensMixin (screens.py) and its server tools."""

import json
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest

from mcp_atlassian.jira import JiraFetcher
from mcp_atlassian.jira.screens import _require_id


@pytest.fixture
def mixin(mock_config, mock_atlassian_jira):
    with patch("atlassian.Jira") as cls:
        cls.return_value = mock_atlassian_jira
        fetcher = JiraFetcher(config=mock_config)
        fetcher.jira = mock_atlassian_jira
        yield fetcher


def _page(values, *, is_last=True):
    return {"values": values, "isLast": is_last}


def _queries(mock_get):
    """Return (path, query-dict) for every GET made through the mock."""
    calls = []
    for call in mock_get.call_args_list:
        parsed = urlparse(call.args[0])
        calls.append((parsed.path, parse_qs(parsed.query)))
    return calls


class TestRequireId:
    @pytest.mark.parametrize("value", ["10200", "customfield_10914", "BB5", "a-b"])
    def test_accepts_plain_identifiers(self, value):
        assert _require_id("x", value) == value

    @pytest.mark.parametrize("value", ["", "  ", None, "../x", "a/b", "1?x=2", "a b"])
    def test_rejects_path_injection(self, value):
        with pytest.raises(ValueError, match="Invalid x"):
            _require_id("x", value)

    def test_strips_whitespace(self):
        assert _require_id("x", " 10 ") == "10"


class TestGetAllPages:
    def test_repeats_array_params(self, mixin):
        mixin.jira.get.return_value = _page([{"id": 1}])
        mixin._get_all_pages("rest/api/3/x", {"projectId": ["1", "2"]})
        _, query = _queries(mixin.jira.get)[0]
        assert query["projectId"] == ["1", "2"]

    def test_follows_pages_until_last(self, mixin):
        mixin.jira.get.side_effect = [
            _page([{"id": 1}, {"id": 2}], is_last=False),
            _page([{"id": 3}]),
        ]
        result = mixin._get_all_pages("rest/api/3/x")
        assert [r["id"] for r in result] == [1, 2, 3]
        assert _queries(mixin.jira.get)[1][1]["startAt"] == ["2"]

    def test_stops_on_empty_page_even_if_not_last(self, mixin):
        mixin.jira.get.side_effect = [_page([], is_last=False)]
        assert mixin._get_all_pages("rest/api/3/x") == []
        assert mixin.jira.get.call_count == 1

    def test_non_dict_response_raises(self, mixin):
        mixin.jira.get.return_value = []
        with pytest.raises(TypeError):
            mixin._get_all_pages("rest/api/3/x")


class TestGetProjectScreenSchemes:
    def _wire(self, mixin, *, schemes, mappings, screen_schemes=None):
        def get(url, params=None):
            path = urlparse(url).path
            if path.endswith("/issuetypescreenscheme/project"):
                return _page(schemes)
            if path.endswith("/issuetypescreenscheme/mapping"):
                return _page(mappings)
            if path.endswith("/screenscheme"):
                return _page(screen_schemes or [])
            if "/project/" in path:
                key = path.rsplit("/", 1)[1]
                return {"id": {"BB5": "10005", "BB6": "10006"}[key]}
            msg = f"unexpected GET {url}"
            raise AssertionError(msg)

        mixin.jira.get.side_effect = get

    def test_resolves_keys_and_groups_shared_screens(self, mixin):
        self._wire(
            mixin,
            schemes=[
                {
                    "issueTypeScreenScheme": {"id": "1", "name": "Shared"},
                    "projectIds": ["10005", "10006"],
                }
            ],
            mappings=[
                {
                    "issueTypeScreenSchemeId": "1",
                    "issueTypeId": "default",
                    "screenSchemeId": "7",
                }
            ],
            screen_schemes=[
                {"id": 7, "name": "SS", "screens": {"default": 100, "create": 101}}
            ],
        )
        result = mixin.get_project_screen_schemes(["BB5", "BB6"], include_screens=True)

        assert [p["project_id"] for p in result["projects"]] == ["10005", "10006"]
        assert result["projects"][0]["issue_type_mappings"] == [
            {"issue_type_id": "default", "screen_scheme_id": "7"}
        ]
        assert result["screen_usage"]["100"]["projects"] == ["BB5", "BB6"]
        assert result["screen_usage"]["101"]["screen_schemes"] == ["7"]

    def test_team_managed_project_has_no_scheme(self, mixin):
        self._wire(mixin, schemes=[], mappings=[])
        result = mixin.get_project_screen_schemes(["10005"])
        assert result["projects"][0]["issue_type_screen_scheme"] is None
        assert result["projects"][0]["issue_type_mappings"] == []
        assert "screen_usage" not in result

    def test_numeric_ids_skip_project_lookup(self, mixin):
        self._wire(mixin, schemes=[], mappings=[])
        mixin.get_project_screen_schemes(["10005"])
        paths = [p for p, _ in _queries(mixin.jira.get)]
        assert not any("/project/" in p for p in paths)

    def test_batches_large_project_lists(self, mixin):
        self._wire(mixin, schemes=[], mappings=[])
        mixin.get_project_screen_schemes([str(10000 + i) for i in range(120)])
        calls = [
            q
            for p, q in _queries(mixin.jira.get)
            if p.endswith("/issuetypescreenscheme/project")
        ]
        assert [len(q["projectId"]) for q in calls] == [50, 50, 20]


class TestScreenReads:
    def test_get_screen_schemes_empty_makes_no_call(self, mixin):
        assert mixin.get_screen_schemes([]) == []
        mixin.jira.get.assert_not_called()

    def test_get_screen_schemes_passes_ids(self, mixin):
        mixin.jira.get.return_value = _page([{"id": 7}])
        mixin.get_screen_schemes(["7", "8"])
        assert _queries(mixin.jira.get)[0][1]["id"] == ["7", "8"]

    def test_tab_fields_path(self, mixin):
        mixin.jira.get.return_value = [{"id": "summary", "name": "Summary"}]
        assert mixin.get_screen_tab_fields("10", "20")[0]["id"] == "summary"
        mixin.jira.get.assert_called_once_with("rest/api/3/screens/10/tabs/20/fields")

    def test_tabs_with_project_key(self, mixin):
        mixin.jira.get.return_value = [{"id": 1, "name": "Field Tab"}]
        mixin.get_screen_tabs("10", project_key="BB5")
        mixin.jira.get.assert_called_once_with(
            "rest/api/3/screens/10/tabs", params={"projectKey": "BB5"}
        )

    def test_available_fields_path(self, mixin):
        mixin.jira.get.return_value = []
        mixin.get_screen_available_fields("10")
        mixin.jira.get.assert_called_once_with("rest/api/3/screens/10/availableFields")

    def test_non_list_response_raises(self, mixin):
        mixin.jira.get.return_value = {"errorMessages": ["x"]}
        with pytest.raises(TypeError):
            mixin.get_screen_tab_fields("10", "20")

    def test_rejects_bad_screen_id_before_any_request(self, mixin):
        with pytest.raises(ValueError):
            mixin.get_screen_tabs("10/../../x")
        mixin.jira.get.assert_not_called()


class TestScreenWrites:
    def test_add_field_payload(self, mixin):
        mixin.jira.post.return_value = {"id": "customfield_10914", "name": "Loc"}
        result = mixin.add_field_to_screen_tab("10", "20", "customfield_10914")
        assert result["id"] == "customfield_10914"
        mixin.jira.post.assert_called_once_with(
            "rest/api/3/screens/10/tabs/20/fields",
            json={"fieldId": "customfield_10914"},
        )

    def test_remove_field_uses_delete(self, mixin):
        mixin.jira.delete.return_value = None
        mixin.remove_field_from_screen_tab("10", "20", "customfield_10914")
        mixin.jira.delete.assert_called_once_with(
            "rest/api/3/screens/10/tabs/20/fields/customfield_10914"
        )


class TestContexts:
    def test_get_field_contexts(self, mixin):
        mixin.jira.get.return_value = _page([{"id": "1", "isGlobalContext": True}])
        assert mixin.get_field_contexts("customfield_10914")[0]["id"] == "1"
        assert _queries(mixin.jira.get)[0][0] == (
            "rest/api/3/field/customfield_10914/context"
        )

    def test_project_mapping_filters_by_context(self, mixin):
        mixin.jira.get.return_value = _page([])
        mixin.get_field_context_project_mapping("customfield_1", ["5", "6"])
        path, query = _queries(mixin.jira.get)[0]
        assert path.endswith("/context/projectmapping")
        assert query["contextId"] == ["5", "6"]

    def test_create_context_global_when_no_projects(self, mixin):
        mixin.jira.post.return_value = {"id": "9", "name": "Ctx"}
        mixin.create_field_context("customfield_1", "Ctx")
        mixin.jira.post.assert_called_once_with(
            "rest/api/3/field/customfield_1/context", json={"name": "Ctx"}
        )

    def test_create_context_with_projects_and_issue_types(self, mixin):
        mixin.jira.post.return_value = {"id": "9"}
        mixin.create_field_context(
            "customfield_1",
            "Ctx",
            description="d",
            project_ids=["10005"],
            issue_type_ids=["3"],
        )
        payload = mixin.jira.post.call_args.kwargs["json"]
        assert payload == {
            "name": "Ctx",
            "description": "d",
            "projectIds": ["10005"],
            "issueTypeIds": ["3"],
        }

    def test_assign_context_uses_put(self, mixin):
        mixin.assign_context_to_project("customfield_1", "9", ["10005"])
        mixin.jira.put.assert_called_once_with(
            "rest/api/3/field/customfield_1/context/9/project",
            data={"projectIds": ["10005"]},
        )

    def test_assign_context_requires_projects(self, mixin):
        with pytest.raises(ValueError, match="at least one"):
            mixin.assign_context_to_project("customfield_1", "9", [])
        mixin.jira.put.assert_not_called()


class TestFields:
    def test_create_custom_field_invalidates_cache(self, mixin):
        mixin._field_ids_cache = [{"id": "summary"}]
        mixin._field_name_to_id_map = {"summary": "summary"}
        mixin.jira.post.return_value = {"id": "customfield_1"}
        mixin.create_custom_field(
            "Loc",
            "com.atlassian.jira.plugin.system.customfieldtypes:url",
            description="d",
        )
        assert mixin.jira.post.call_args.kwargs["json"] == {
            "name": "Loc",
            "type": "com.atlassian.jira.plugin.system.customfieldtypes:url",
            "description": "d",
        }
        assert mixin._field_ids_cache is None
        assert mixin._field_name_to_id_map is None

    def test_update_field_requires_a_change(self, mixin):
        with pytest.raises(ValueError, match="at least one"):
            mixin.update_field("customfield_1")
        mixin.jira.put.assert_not_called()

    def test_update_field_sends_only_given_values(self, mixin):
        mixin._field_ids_cache = [{"id": "x"}]
        mixin.update_field("customfield_1", name="Location (google-maps)")
        mixin.jira.put.assert_called_once_with(
            "rest/api/3/field/customfield_1", data={"name": "Location (google-maps)"}
        )
        assert mixin._field_ids_cache is None

    def test_field_configuration_scheme_dedupes_project_ids(self, mixin):
        mixin.jira.get.return_value = _page([])
        mixin.get_project_field_configuration_scheme(["10005", "10005"])
        assert _queries(mixin.jira.get)[0][1]["projectId"] == ["10005"]

    def test_field_configuration_items_path(self, mixin):
        mixin.jira.get.return_value = _page([{"id": "summary", "isRequired": True}])
        items = mixin.get_field_configuration_items("10000")
        assert items[0]["isRequired"] is True
        assert _queries(mixin.jira.get)[0][0] == (
            "rest/api/3/fieldconfiguration/10000/fields"
        )


WRITE_TOOLS = [
    ("add_field_to_screen_tab", {"screen_id": "1", "tab_id": "2", "field_id": "f"}),
    (
        "remove_field_from_screen_tab",
        {"screen_id": "1", "tab_id": "2", "field_id": "f"},
    ),
    ("create_field_context", {"field_id": "f", "name": "n"}),
    (
        "assign_context_to_project",
        {"field_id": "f", "context_id": "1", "project_ids": "10005"},
    ),
    ("create_custom_field", {"name": "n", "field_type": "t"}),
    ("update_field", {"field_id": "f", "name": "n"}),
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

    async def test_write_tool_runs_when_not_read_only(self):
        from mcp_atlassian.servers import jira as server

        fetcher = MagicMock()
        fetcher.add_field_to_screen_tab.return_value = {"id": "f", "name": "F"}
        with patch.object(server, "get_jira_fetcher", AsyncMock(return_value=fetcher)):
            out = await server.add_field_to_screen_tab.fn(
                _ctx(read_only=False), screen_id="1", tab_id="2", field_id="f"
            )
        data = json.loads(out)
        assert data["success"] is True
        assert data["field"] == {"id": "f", "name": "F"}

    async def test_read_tool_reports_permission_error(self):
        from requests.exceptions import HTTPError

        from mcp_atlassian.servers import jira as server

        response = MagicMock(status_code=403)
        fetcher = MagicMock()
        fetcher.get_screen_tabs.side_effect = HTTPError("Forbidden", response=response)
        with patch.object(server, "get_jira_fetcher", AsyncMock(return_value=fetcher)):
            out = await server.get_screen_tabs.fn(MagicMock(), screen_id="1")
        data = json.loads(out)
        assert data["success"] is False
        assert "Administer Jira" in data["error"]

    async def test_project_screen_schemes_splits_csv(self):
        from mcp_atlassian.servers import jira as server

        fetcher = MagicMock()
        fetcher.get_project_screen_schemes.return_value = {"projects": []}
        with patch.object(server, "get_jira_fetcher", AsyncMock(return_value=fetcher)):
            await server.get_project_screen_schemes.fn(
                MagicMock(), projects="BB5, BB6 ,", include_screens=True
            )
        fetcher.get_project_screen_schemes.assert_called_once_with(
            ["BB5", "BB6"], include_screens=True
        )
