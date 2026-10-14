"""Tests for SecurityMixin (security.py) and its server tools.

Response fixtures follow Atlassian's OpenAPI definition (and were checked
against a live Jira Cloud site): scheme lists are wrapped in
``issueSecuritySchemes``, levels/members/project mappings are paginated under
``values``, and the project mapping only carries ``projectId`` and
``issueSecuritySchemeId``.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest
from requests.exceptions import HTTPError

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
    return {
        "maxResults": 100,
        "startAt": 0,
        "total": len(values),
        "isLast": is_last,
        "values": values,
    }


def _mapping(project_id="10036", scheme_id="10019"):
    return {"projectId": project_id, "issueSecuritySchemeId": scheme_id}


def _level(level_id, name="Felles", *, default=False, scheme_id="10019"):
    return {
        "self": f"https://x.atlassian.net/rest/api/3/securitylevel/{level_id}",
        "id": str(level_id),
        "name": name,
        "description": "",
        "isDefault": default,
        "issueSecuritySchemeId": scheme_id,
    }


def _scheme(scheme_id=10019, default_level=10014, levels=None):
    body = {
        "self": f"https://x.atlassian.net/rest/api/3/issuesecurityschemes/{scheme_id}",
        "id": scheme_id,
        "name": "SST - Security scheme",
        "description": "",
        "levels": levels
        if levels is not None
        else [
            {"id": "10014", "name": "Felles", "description": ""},
            {"id": "10015", "name": "Hide", "description": ""},
        ],
    }
    if default_level is not None:
        body["defaultSecurityLevelId"] = default_level
    return body


def _member(member_id, level_id="10014", role_id="10008", name="Prosjektmedarbeider"):
    return {
        "id": str(member_id),
        "issueSecurityLevelId": str(level_id),
        "issueSecuritySchemeId": "10019",
        "holder": {
            "type": "projectRole",
            "parameter": role_id,
            "value": role_id,
            "projectRole": {"id": int(role_id), "name": name},
            "expand": "projectRole",
        },
    }


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


class TestReads:
    def test_list_schemes_reads_wrapped_response(self, mixin):
        mixin.jira.get.return_value = {
            "issueSecuritySchemes": [
                {"id": 10012, "name": "SSP: Simplified Issue Security Scheme"},
                {
                    "id": 10019,
                    "name": "SST - Security scheme",
                    "description": "",
                    "defaultSecurityLevelId": 10014,
                },
            ]
        }
        result = mixin.get_issue_security_schemes()
        assert [s["id"] for s in result] == ["10012", "10019"]
        assert result[0]["default_level_id"] is None
        assert result[1]["default_level_id"] == "10014"

    def test_list_schemes_unexpected_shape_raises(self, mixin):
        mixin.jira.get.return_value = _page([])
        with pytest.raises(TypeError, match="'issueSecuritySchemes'"):
            mixin.get_issue_security_schemes()

    def test_scheme_for_project_uses_mapping_then_scheme_endpoint(self, mixin):
        mixin.jira.get.side_effect = [
            _page([_mapping("10052", "10019")]),
            _scheme(),
        ]
        result = mixin.get_issue_security_scheme_for_project("10052")
        assert result["scheme_id"] == "10019"
        assert result["name"] == "SST - Security scheme"
        assert result["default_level_id"] == "10014"
        assert [lvl["id"] for lvl in result["levels"]] == ["10014", "10015"]
        (path1, query1), (path2, _) = _queries(mixin.jira.get)
        assert path1 == "rest/api/3/issuesecurityschemes/project"
        assert query1["projectId"] == ["10052"]
        assert path2 == "rest/api/3/issuesecurityschemes/10019"

    def test_scheme_for_project_resolves_project_key(self, mixin):
        mixin.jira.get.side_effect = [
            {"id": "10052", "key": "SST"},
            _page([_mapping("10052", "10019")]),
            _scheme(),
        ]
        mixin.get_issue_security_scheme_for_project("SST")
        paths = [path for path, _ in _queries(mixin.jira.get)]
        assert paths[0] == "rest/api/3/project/SST"

    def test_project_without_scheme_returns_none(self, mixin):
        mixin.jira.get.return_value = _page([])
        result = mixin.get_issue_security_scheme_for_project("10036")
        assert result["scheme_id"] is None
        assert result["levels"] == []
        assert mixin.jira.get.call_count == 1

    def test_scheme_without_default_level(self, mixin):
        mixin.jira.get.side_effect = [_page([_mapping()]), _scheme(default_level=None)]
        assert (
            mixin.get_issue_security_scheme_for_project("10036")["default_level_id"]
            is None
        )

    def test_levels_use_is_default_flag(self, mixin):
        mixin.jira.get.return_value = _page(
            [_level(10014, "Felles", default=True), _level(10015, "Hide")]
        )
        result = mixin.get_issue_security_levels("10019")
        assert result == [
            {"id": "10014", "name": "Felles", "description": "", "is_default": True},
            {"id": "10015", "name": "Hide", "description": "", "is_default": False},
        ]
        path, query = _queries(mixin.jira.get)[0]
        assert path == "rest/api/3/issuesecurityschemes/level"
        assert query["schemeId"] == ["10019"]

    def test_level_members_include_id_and_readable_name(self, mixin):
        mixin.jira.get.return_value = _page(
            [_member(10040), _member(10042, role_id="10003", name="addons")]
        )
        result = mixin.get_security_level_members("10014")
        assert result["total"] == 2
        assert result["members"][0] == {
            "member_id": "10040",
            "type": "projectRole",
            "parameter": "10008",
            "value": "10008",
            "name": "Prosjektmedarbeider",
        }
        path, query = _queries(mixin.jira.get)[0]
        assert path == "rest/api/3/issuesecurityschemes/level/member"
        assert query["levelId"] == ["10014"]
        assert query["expand"] == ["all"]

    def test_level_members_follow_pagination(self, mixin):
        mixin.jira.get.side_effect = [
            _page([_member(1)], is_last=False),
            _page([_member(2)]),
        ]
        result = mixin.get_security_level_members("10014")
        assert [m["member_id"] for m in result["members"]] == ["1", "2"]
        assert _queries(mixin.jira.get)[1][1]["startAt"] == ["1"]

    def test_level_members_permission_error(self, mixin):
        mixin.jira.get.side_effect = HTTPError(
            "Forbidden", response=MagicMock(status_code=403)
        )
        with pytest.raises(MCPAtlassianAuthenticationError):
            mixin.get_security_level_members("10002")

    def test_level_members_reject_non_numeric_level(self, mixin):
        with pytest.raises(ValueError, match="must be numeric"):
            mixin.get_security_level_members("10002&x=1")
        mixin.jira.get.assert_not_called()


class TestWrites:
    def test_associate_dry_run_reports_unmapped_levels(self, mixin):
        mixin.jira.get.side_effect = [
            {"id": "10036", "key": "BB5O"},
            _page([_mapping("10036", "10019")]),
            _scheme(),
            _page([_level(20001, "Ny", scheme_id="10009")]),
        ]
        result = mixin.set_project_issue_security_scheme(
            "BB5O", "10009", [{"oldLevelId": "10014", "newLevelId": "20001"}]
        )
        assert result["dry_run"] is True
        assert result["would_apply"] == {
            "projectId": "10036",
            "schemeId": "10009",
            "oldToNewSecurityLevelMappings": [
                {"oldLevelId": "10014", "newLevelId": "20001"}
            ],
        }
        assert result["unmapped_levels"] == ["10015"]
        assert result["new_scheme_levels"][0]["id"] == "20001"
        mixin.jira._session.put.assert_not_called()

    def test_associate_dry_run_for_project_without_scheme(self, mixin):
        mixin.jira.get.side_effect = [
            {"id": "10036", "key": "BB5O"},
            _page([]),
            _page([_level(10014, default=True)]),
        ]
        result = mixin.set_project_issue_security_scheme("BB5O", "10019")
        assert result["current"]["scheme_id"] is None
        assert result["unmapped_levels"] == []

    def _wire_session(self, mixin, response):
        mixin.jira.url = "https://x.atlassian.net"
        mixin.jira.url_joiner = lambda base, path, trailing=None: f"{base}/{path}"
        mixin.jira._session.put.return_value = response

    def test_associate_execute_returns_task_location(self, mixin):
        mixin.jira.get.return_value = {"id": "10036", "key": "BB5O"}
        self._wire_session(
            mixin,
            MagicMock(
                status_code=303,
                headers={"location": "https://x.atlassian.net/rest/api/3/task/99999"},
            ),
        )
        result = mixin.set_project_issue_security_scheme("BB5O", "10009", dry_run=False)
        assert result["task_id"] == "99999"
        call = mixin.jira._session.put.call_args
        assert call.args[0] == (
            "https://x.atlassian.net/rest/api/3/issuesecurityschemes/project"
        )
        assert call.kwargs["json"] == {"projectId": "10036", "schemeId": "10009"}
        assert call.kwargs["allow_redirects"] is False
        mixin.jira.put.assert_not_called()

    def test_associate_execute_raises_on_http_error(self, mixin):
        response = MagicMock(status_code=403, headers={})
        response.raise_for_status.side_effect = HTTPError("Forbidden")
        self._wire_session(mixin, response)
        with pytest.raises(HTTPError):
            mixin.set_project_issue_security_scheme("10036", "10009", dry_run=False)

    def test_associate_null_clears_association(self, mixin):
        mixin.jira.get.return_value = _page([])
        result = mixin.set_project_issue_security_scheme("10036", None)
        assert result["would_apply"]["schemeId"] is None

    def test_associate_rejects_bad_mapping(self, mixin):
        with pytest.raises(ValueError, match="must be numeric"):
            mixin.set_project_issue_security_scheme(
                "10036", "10009", [{"oldLevelId": "10014"}]
            )

    def test_add_member_dry_run_flags_duplicate(self, mixin):
        mixin.jira.get.return_value = _page([_member(10040)])
        result = mixin.set_issue_security_level_member(
            "10019", "10014", "projectRole", "10008"
        )
        assert result["duplicate"] is True
        assert result["existing_member_ids"] == ["10040"]
        # The documented spelling is lowercase, even though GET returns projectRole.
        assert result["would_apply"] == {
            "members": [{"type": "projectrole", "parameter": "10008"}]
        }
        mixin.jira.put.assert_not_called()

    def test_add_member_dry_run_new_member_is_not_duplicate(self, mixin):
        mixin.jira.get.return_value = _page([_member(10040)])
        result = mixin.set_issue_security_level_member(
            "10019", "10014", "projectrole", "10009"
        )
        assert result["duplicate"] is False

    def test_add_reporter_needs_no_parameter(self, mixin):
        mixin.jira.get.return_value = _page([])
        result = mixin.set_issue_security_level_member("10019", "10014", "reporter")
        assert result["would_apply"] == {"members": [{"type": "reporter"}]}

    def test_add_member_execute_uses_members_array(self, mixin):
        mixin.set_issue_security_level_member(
            "10019", "10014", "group", "jira-admins", dry_run=False
        )
        mixin.jira.put.assert_called_once_with(
            "rest/api/3/issuesecurityschemes/10019/level/10014/member",
            data={"members": [{"type": "group", "parameter": "jira-admins"}]},
        )

    @pytest.mark.parametrize("member_type", ["role", "allUsers", ""])
    def test_add_member_rejects_unknown_type(self, mixin, member_type):
        with pytest.raises(ValueError, match="Invalid member_type"):
            mixin.set_issue_security_level_member("10019", "10014", member_type, "x")
        mixin.jira.put.assert_not_called()

    def test_add_member_requires_parameter_for_group(self, mixin):
        with pytest.raises(ValueError, match="needs a parameter"):
            mixin.set_issue_security_level_member("10019", "10014", "group")

    def test_remove_member_dry_run_requires_existing_member(self, mixin):
        mixin.jira.get.return_value = _page([_member(10040)])
        result = mixin.remove_issue_security_level_member("10019", "10014", "10040")
        assert result["current"]["member_id"] == "10040"
        assert result["current"]["name"] == "Prosjektmedarbeider"
        mixin.jira.delete.assert_not_called()
        with pytest.raises(ValueError, match="not a member"):
            mixin.remove_issue_security_level_member("10019", "10014", "10099")

    def test_remove_member_execute_deletes(self, mixin):
        mixin.remove_issue_security_level_member(
            "10019", "10014", "10040", dry_run=False
        )
        mixin.jira.delete.assert_called_once_with(
            "rest/api/3/issuesecurityschemes/10019/level/10014/member/10040"
        )

    def test_default_level_dry_run_validates_level_belongs_to_scheme(self, mixin):
        mixin.jira.get.return_value = _scheme()
        result = mixin.set_default_issue_security_level("10019", "10015")
        assert result["current"]["default_level_id"] == "10014"
        assert result["would_apply"] == {
            "defaultValues": [
                {"issueSecuritySchemeId": "10019", "defaultLevelId": "10015"}
            ]
        }
        mixin.jira.put.assert_not_called()
        with pytest.raises(ValueError, match="does not belong"):
            mixin.set_default_issue_security_level("10019", "10002")

    def test_default_level_execute_and_reset(self, mixin):
        mixin.set_default_issue_security_level("10019", None, dry_run=False)
        mixin.jira.put.assert_called_once_with(
            "rest/api/3/issuesecurityschemes/level/default",
            data={
                "defaultValues": [
                    {"issueSecuritySchemeId": "10019", "defaultLevelId": None}
                ]
            },
        )

    def test_set_issue_security_dry_run_reads_only_security_field(self, mixin):
        mixin.jira.get.return_value = {
            "id": "10100",
            "key": "BB5O-1",
            "fields": {"security": {"id": "10014", "name": "Felles"}},
        }
        result = mixin.set_issue_security("BB5O-1", "10015")
        assert result["current"]["id"] == "10014"
        assert result["would_apply"] == {"fields": {"security": {"id": "10015"}}}
        path, query = _queries(mixin.jira.get)[0]
        assert path == "rest/api/3/issue/BB5O-1"
        assert query["fields"] == ["security"]
        mixin.jira.put.assert_not_called()

    def test_set_issue_security_clear_sends_null_field(self, mixin):
        result = mixin.set_issue_security("BB5O-1", None, dry_run=False)
        assert result["security_level_id"] is None
        mixin.jira.put.assert_called_once_with(
            "rest/api/3/issue/BB5O-1", data={"fields": {"security": None}}
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

    @pytest.mark.parametrize(("tool", "_kwargs"), WRITE_TOOLS)
    async def test_dry_run_defaults_to_true(self, tool, _kwargs):
        from mcp_atlassian.servers import jira as server

        schema = getattr(server, tool).parameters["properties"]["dry_run"]
        assert schema["default"] is True
        assert "true (the default)" in schema["description"]

    async def test_read_tool_reports_permission_error(self):
        from mcp_atlassian.servers import jira as server

        fetcher = MagicMock()
        fetcher.get_issue_security_levels.side_effect = HTTPError(
            "Forbidden", response=MagicMock(status_code=403)
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
