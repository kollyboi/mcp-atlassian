"""Tests for SecurityMixin (security.py) and its server tools."""

import json
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

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


class TestSecuritySchemeReads:
    def test_list_schemes(self, mixin):
        mixin.jira.get.return_value = _page(
            [{"id": 10001, "name": "S", "defaultLevelId": 10002}]
        )
        result = mixin.get_issue_security_schemes()
        assert result[0]["id"] == "10001"
        assert result[0]["default_level_id"] == "10002"

    def test_scheme_for_project_includes_levels(self, mixin):
        mixin.jira.get.side_effect = [
            {"id": "10005", "key": "BB5O"},
            _page(
                [
                    {
                        "projectId": "10005",
                        "issueSecuritySchemeId": "10001",
                        "issueSecuritySchemeName": "S",
                    }
                ]
            ),
            _page([{"id": "10002", "name": "Confidential", "projectCount": 3}]),
        ]
        result = mixin.get_issue_security_scheme_for_project("BB5O")
        assert result["project_id"] == "10005"
        assert result["scheme_id"] == "10001"
        assert result["levels"][0]["id"] == "10002"
        path, query = _queries(mixin.jira.get)[2]
        assert path == "rest/api/3/issuesecurityschemes/level"
        assert query["schemeId"] == ["10001"]

    def test_project_without_scheme_returns_none(self, mixin):
        mixin.jira.get.side_effect = [
            {"id": "10005", "key": "BB5O"},
            _page([]),
        ]
        result = mixin.get_issue_security_scheme_for_project("BB5O")
        assert result["scheme_id"] is None
        assert result["levels"] == []

    def test_level_members_include_member_id(self, mixin):
        mixin.jira.get.return_value = _page(
            [
                {
                    "id": "10003",
                    "holder": {"type": "group", "parameter": "jira-admins"},
                }
            ]
        )
        result = mixin.get_security_level_members("10002")
        assert result["members"][0]["member_id"] == "10003"
        assert result["members"][0]["parameter"] == "jira-admins"

    def test_level_members_permission_error(self, mixin):
        from requests.exceptions import HTTPError

        response = MagicMock(status_code=403)
        mixin.jira.get.side_effect = HTTPError("Forbidden", response=response)
        with pytest.raises(MCPAtlassianAuthenticationError):
            mixin.get_security_level_members("10002")


class TestSecurityWrites:
    def test_associate_dry_run_reports_unmapped_levels(self, mixin):
        mixin.jira.get.side_effect = [
            {"id": "10005", "key": "BB5O"},
            _page([{"projectId": "10005", "issueSecuritySchemeId": "10001"}]),
            _page([{"id": "10002", "name": "Confidential"}]),
        ]
        result = mixin.set_project_issue_security_scheme(
            "BB5O", "10009", [{"oldLevelId": "10002", "newLevelId": "10010"}]
        )
        assert result["dry_run"] is True
        assert result["would_apply"]["oldToNewSecurityLevelMappings"] == [
            {"oldLevelId": "10002", "newLevelId": "10010"}
        ]
        mixin.jira.put.assert_not_called()

    def test_associate_dry_run_flags_unmapped_level(self, mixin):
        mixin.jira.get.side_effect = [
            {"id": "10005", "key": "BB5O"},
            _page([{"projectId": "10005", "issueSecuritySchemeId": "10001"}]),
            _page(
                [
                    {"id": "10002", "name": "Confidential"},
                    {"id": "10003", "name": "Internal"},
                ]
            ),
        ]
        result = mixin.set_project_issue_security_scheme(
            "BB5O", "10009", [{"oldLevelId": "10002", "newLevelId": "10010"}]
        )
        assert result["unmapped_levels"] == ["10003"]

    def test_associate_execute_returns_task_location(self, mixin):
        mixin.jira.get.side_effect = [
            {"id": "10005", "key": "BB5O"},
            _page([]),
        ]
        mixin.jira.url = "https://x.atlassian.net"
        mixin.jira.url_joiner = lambda base, path, trailing=None: f"{base}/{path}"
        response = MagicMock(
            status_code=303,
            headers={"location": "https://api.atlassian.com/rest/api/3/task/99999"},
        )
        mixin.jira._session.put.return_value = response
        result = mixin.set_project_issue_security_scheme("BB5O", "10009", dry_run=False)
        assert result["task_id"] == "99999"
        call = mixin.jira._session.put.call_args
        assert call.args[0] == (
            "https://x.atlassian.net/rest/api/3/issuesecurityschemes/project"
        )
        assert call.kwargs["json"] == {"projectId": "10005", "schemeId": "10009"}
        assert call.kwargs["allow_redirects"] is False
        mixin.jira.put.assert_not_called()

    def test_associate_execute_raises_on_http_error(self, mixin):
        from requests.exceptions import HTTPError

        mixin.jira.url = "https://x.atlassian.net"
        mixin.jira.url_joiner = lambda base, path, trailing=None: f"{base}/{path}"
        response = MagicMock(status_code=403, headers={})
        response.raise_for_status.side_effect = HTTPError("Forbidden")
        mixin.jira._session.put.return_value = response
        with pytest.raises(HTTPError):
            mixin.set_project_issue_security_scheme("10005", "10009", dry_run=False)

    def test_associate_null_clears_association(self, mixin):
        mixin.jira.get.return_value = _page([])
        result = mixin.set_project_issue_security_scheme("10005", None)
        assert result["would_apply"]["schemeId"] is None

    def test_add_member_dry_run_flags_duplicate(self, mixin):
        mixin.jira.get.return_value = _page(
            [{"id": "10003", "holder": {"type": "group", "parameter": "jira-admins"}}]
        )
        result = mixin.set_issue_security_level_member(
            "10001", "10002", "group", "jira-admins"
        )
        assert result["duplicate"] is True
        assert result["existing_member_ids"] == ["10003"]
        mixin.jira.put.assert_not_called()

    def test_add_member_execute_uses_members_array(self, mixin):
        mixin.set_issue_security_level_member(
            "10001", "10002", "group", "jira-admins", dry_run=False
        )
        mixin.jira.put.assert_called_once_with(
            "rest/api/3/issuesecurityschemes/10001/level/10002/member",
            data={"members": [{"type": "group", "parameter": "jira-admins"}]},
        )

    def test_add_member_rejects_unknown_type(self, mixin):
        with pytest.raises(ValueError, match="Invalid member_type"):
            mixin.set_issue_security_level_member("10001", "10002", "role", "admins")
        mixin.jira.put.assert_not_called()

    def test_remove_member_dry_run_requires_existing_member(self, mixin):
        mixin.jira.get.return_value = _page(
            [{"id": "10003", "holder": {"type": "group", "parameter": "admins"}}]
        )
        result = mixin.remove_issue_security_level_member("10001", "10002", "10003")
        assert result["current"]["member_id"] == "10003"
        mixin.jira.delete.assert_not_called()

        with pytest.raises(ValueError, match="not a member"):
            mixin.remove_issue_security_level_member("10001", "10002", "10004")

    def test_remove_member_execute_deletes(self, mixin):
        mixin.remove_issue_security_level_member(
            "10001", "10002", "10003", dry_run=False
        )
        mixin.jira.delete.assert_called_once_with(
            "rest/api/3/issuesecurityschemes/10001/level/10002/member/10003"
        )

    def test_default_level_dry_run_and_execute(self, mixin):
        mixin.jira.get.return_value = _page(
            [{"id": "10001", "name": "S", "defaultLevelId": "10002"}]
        )
        result = mixin.set_default_issue_security_level("10001", "10005")
        assert result["current"]["default_level_id"] == "10002"
        assert result["would_apply"]["defaultValues"][0]["defaultLevelId"] == "10005"
        mixin.jira.put.assert_not_called()

        mixin.set_default_issue_security_level("10001", None, dry_run=False)
        mixin.jira.put.assert_called_once_with(
            "rest/api/3/issuesecurityschemes/level/default",
            data={
                "defaultValues": [
                    {"issueSecuritySchemeId": "10001", "defaultLevelId": None}
                ]
            },
        )

    def test_set_issue_security_dry_run_and_execute(self, mixin):
        mixin.jira.get.return_value = {
            "id": "10100",
            "key": "BB5O-1",
            "fields": {"security": {"id": "10002", "name": "Confidential"}},
        }
        result = mixin.set_issue_security("BB5O-1", "10003")
        assert result["current"]["id"] == "10002"
        assert result["would_apply"]["fields"]["security"]["id"] == "10003"
        mixin.jira.put.assert_not_called()

        result = mixin.set_issue_security("BB5O-1", None, dry_run=False)
        assert result["security_level_id"] is None
        mixin.jira.put.assert_called_once_with(
            "rest/api/3/issue/BB5O-1", data={"fields": {"security": {"id": None}}}
        )


@pytest.mark.anyio
class TestServerTools:
    WRITE_TOOLS = [
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

    async def test_read_tool_reports_permission_error(self):
        from requests.exceptions import HTTPError

        from mcp_atlassian.servers import jira as server

        response = MagicMock(status_code=403)
        fetcher = MagicMock()
        fetcher.get_issue_security_levels.side_effect = HTTPError(
            "Forbidden", response=response
        )
        with patch.object(server, "get_jira_fetcher", AsyncMock(return_value=fetcher)):
            out = await server.get_issue_security_levels.fn(
                MagicMock(), scheme_id="10001"
            )
        data = json.loads(out)
        assert data["success"] is False
        assert "Administer Jira" in data["error"]

    async def test_security_level_members_reports_auth_error(self):
        from mcp_atlassian.servers import jira as server

        fetcher = MagicMock()
        fetcher.get_security_level_members.side_effect = (
            MCPAtlassianAuthenticationError("no access")
        )
        with patch.object(server, "get_jira_fetcher", AsyncMock(return_value=fetcher)):
            out = await server.get_security_level_members.fn(
                MagicMock(), level_id="10002"
            )
        data = json.loads(out)
        assert data["success"] is False
        assert data["error"] == "no access"
