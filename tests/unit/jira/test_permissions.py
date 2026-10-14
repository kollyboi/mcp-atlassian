"""Tests for PermissionsMixin (permissions.py) and its server tools."""

import json
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest

from mcp_atlassian.jira import JiraFetcher
from mcp_atlassian.jira.permissions import _require_numeric_id


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


def _ctx(read_only: bool):
    ctx = MagicMock()
    ctx.request_context.lifespan_context = {
        "app_lifespan_context": MagicMock(read_only=read_only)
    }
    return ctx


class TestRequireNumericId:
    @pytest.mark.parametrize("value", ["10200", 10200])
    def test_accepts_numeric_ids(self, value):
        assert _require_numeric_id("x", value) == "10200"

    @pytest.mark.parametrize("value", ["BB5O", "10a", "", None, "a/b"])
    def test_rejects_non_numeric_ids(self, value):
        with pytest.raises(ValueError, match="must be numeric"):
            _require_numeric_id("x", value)


class TestPermissionSchemeReads:
    def test_get_project_permission_scheme(self, mixin):
        mixin.jira.get.return_value = {
            "id": 10001,
            "name": "BB5O Scheme",
            "key": "BB5O",
            "description": "d",
            "projectId": "10005",
        }
        result = mixin.get_permission_scheme_for_project("BB5O")
        assert result["scheme_id"] == "10001"
        assert result["project_id"] == "10005"
        assert _queries(mixin.jira.get)[0][0] == (
            "rest/api/3/project/BB5O/permissionscheme"
        )

    def test_grants_expand_all_and_filter(self, mixin):
        mixin.jira.get.return_value = _page(
            [
                {
                    "id": 111,
                    "key": "SET_ISSUE_SECURITY",
                    "name": "Set Issue Security",
                    "holder": {"type": "projectRole", "parameter": "10003"},
                }
            ]
        )
        result = mixin.get_permission_scheme_grants("10001", "SET_ISSUE_SECURITY")
        assert result["total"] == 1
        assert result["grants"][0]["holder"]["parameter"] == "10003"
        path, query = _queries(mixin.jira.get)[0]
        assert path == "rest/api/3/permissionscheme/10001/permission"
        assert query["expand"] == ["all"]
        assert query["permission"] == ["SET_ISSUE_SECURITY"]

    def test_resolves_permission_key_to_id(self, mixin):
        mixin.jira.get.side_effect = [
            _page(
                [{"id": 100, "key": "SET_ISSUE_SECURITY", "name": "Set Issue Security"}]
            ),
            _page([]),
        ]
        result = mixin.set_permission_grant(
            "10001", "SET_ISSUE_SECURITY", "projectRole", "10003"
        )
        assert result["would_apply"]["permission"] == "100"

    def test_unknown_permission_key_is_passed_through(self, mixin):
        mixin.jira.get.side_effect = [_page([]), _page([])]
        result = mixin.set_permission_grant(
            "10001", "CUSTOM_PERMISSION", "group", "admins"
        )
        assert result["would_apply"]["permission"] == "CUSTOM_PERMISSION"

    def test_async_task_accepts_url_or_id(self, mixin):
        mixin.jira.get.return_value = {
            "id": "12345",
            "location": "https://x/rest/api/3/task/12345",
        }
        assert mixin.get_async_task("12345")["task_id"] == "12345"
        assert _queries(mixin.jira.get)[0][0] == "rest/api/3/task/12345"
        mixin.get_async_task("https://x/rest/api/3/task/12345")
        assert _queries(mixin.jira.get)[1][0].endswith("task/12345")


class TestPermissionWrites:
    def test_dry_run_is_default_and_makes_no_write(self, mixin):
        mixin.jira.get.side_effect = [
            {"id": "10005", "key": "BB5O"},
            {"id": 10001, "name": "Old", "projectId": "10005", "key": "BB5O"},
        ]
        result = mixin.set_project_permission_scheme("BB5O", "10002")
        assert result["dry_run"] is True
        assert result["would_apply"] == {"id": 10002}
        assert result["current"]["scheme_id"] == "10001"
        mixin.jira.put.assert_not_called()

    def test_execute_sends_put(self, mixin):
        mixin.jira.get.return_value = {"id": "10005", "key": "BB5O"}
        mixin.jira.put.return_value = {"id": 10002, "name": "New"}
        result = mixin.set_project_permission_scheme("BB5O", "10002", dry_run=False)
        assert result["dry_run"] is False
        mixin.jira.put.assert_called_once_with(
            "rest/api/3/project/10005/permissionscheme", data={"id": 10002}
        )

    def test_grant_dry_run_flags_duplicate(self, mixin):
        mixin.jira.get.return_value = _page(
            [
                {
                    "id": 111,
                    "key": "SET_ISSUE_SECURITY",
                    "holder": {"type": "projectRole", "parameter": "10003"},
                }
            ]
        )
        result = mixin.set_permission_grant("10001", "100", "projectRole", "10003")
        assert result["duplicate"] is True
        assert result["existing_grant_ids"] == ["111"]
        mixin.jira.post.assert_not_called()

    def test_grant_execute_posts(self, mixin):
        mixin.jira.post.return_value = {"id": 222, "key": "SET_ISSUE_SECURITY"}
        result = mixin.set_permission_grant(
            "10001", "100", "group", "jira-admins", dry_run=False
        )
        assert result["grant_id"] == "222"
        mixin.jira.post.assert_called_once_with(
            "rest/api/3/permissionscheme/10001/permission",
            data={
                "holder": {"type": "group", "parameter": "jira-admins"},
                "permission": "100",
            },
        )

    def test_grant_rejects_unknown_holder_type(self, mixin):
        with pytest.raises(ValueError, match="Invalid holder_type"):
            mixin.set_permission_grant("10001", "100", "role", "10003")
        mixin.jira.post.assert_not_called()

    def test_remove_grant_dry_run_reads_grant(self, mixin):
        mixin.jira.get.return_value = {"id": 111, "key": "SET_ISSUE_SECURITY"}
        result = mixin.remove_permission_grant("10001", "111")
        assert result["dry_run"] is True
        assert result["current"]["id"] == 111
        mixin.jira.delete.assert_not_called()

    def test_remove_grant_execute_deletes(self, mixin):
        result = mixin.remove_permission_grant("10001", "111", dry_run=False)
        assert result["success"] is True
        mixin.jira.delete.assert_called_once_with(
            "rest/api/3/permissionscheme/10001/permission/111"
        )


@pytest.mark.anyio
class TestServerTools:
    WRITE_TOOLS = [
        (
            "set_project_permission_scheme",
            {"project_key": "BB5O", "scheme_id": "10002"},
        ),
        (
            "set_permission_grant",
            {
                "scheme_id": "10001",
                "permission": "SET_ISSUE_SECURITY",
                "holder_type": "group",
                "holder_parameter": "jira-admins",
            },
        ),
        ("remove_permission_grant", {"scheme_id": "10001", "grant_id": "111"}),
        (
            "set_project_issue_security_scheme",
            {"project_key": "BB5O", "scheme_id": "10001"},
        ),
        (
            "set_issue_security_level_member",
            {
                "scheme_id": "10001",
                "level_id": "10002",
                "member_type": "group",
                "parameter": "admins",
            },
        ),
        (
            "remove_issue_security_level_member",
            {"scheme_id": "10001", "level_id": "10002", "member_id": "10003"},
        ),
        (
            "set_default_issue_security_level",
            {"scheme_id": "10001", "level_id": "10002"},
        ),
        ("set_issue_security", {"issue_key": "BB5O-1", "level_id": "10002"}),
    ]

    @pytest.mark.parametrize(("tool", "kwargs"), WRITE_TOOLS)
    async def test_write_tools_blocked_in_read_only_mode(self, tool, kwargs):
        from mcp_atlassian.servers import jira as server

        fetcher_getter = AsyncMock()
        with patch.object(server, "get_jira_fetcher", fetcher_getter):
            fn = getattr(server, tool).fn
            with pytest.raises(ValueError, match="read-only mode"):
                await fn(_ctx(read_only=True), **kwargs)
        fetcher_getter.assert_not_called()

    async def test_dry_run_default_is_true_on_all_write_tools(self):
        from mcp_atlassian.servers import jira as server

        for tool, _ in self.WRITE_TOOLS:
            fn = getattr(server, tool)
            schema = fn.parameters["properties"]["dry_run"]
            assert schema["default"] is True, tool
            assert "true (the default)" in schema["description"], tool

    async def test_dry_run_tool_does_not_call_mutation(self):
        from mcp_atlassian.servers import jira as server

        fetcher = MagicMock()
        fetcher.set_permission_grant.return_value = {
            "success": True,
            "dry_run": True,
            "action": "set_permission_grant",
            "would_apply": {"permission": "100"},
        }
        with patch.object(server, "get_jira_fetcher", AsyncMock(return_value=fetcher)):
            out = await server.set_permission_grant.fn(
                MagicMock(),
                scheme_id="10001",
                permission="SET_ISSUE_SECURITY",
                holder_type="group",
                holder_parameter="jira-admins",
            )
        data = json.loads(out)
        assert data["dry_run"] is True
        assert data["would_apply"] == {"permission": "100"}

    async def test_read_tool_reports_permission_error(self):
        from requests.exceptions import HTTPError

        from mcp_atlassian.servers import jira as server

        response = MagicMock(status_code=403)
        fetcher = MagicMock()
        fetcher.get_permission_scheme_grants.side_effect = HTTPError(
            "Forbidden", response=response
        )
        with patch.object(server, "get_jira_fetcher", AsyncMock(return_value=fetcher)):
            out = await server.get_permission_scheme_grants.fn(
                MagicMock(), scheme_id="10001"
            )
        data = json.loads(out)
        assert data["success"] is False
        assert "Administer Jira" in data["error"]
