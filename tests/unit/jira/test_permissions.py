"""Tests for PermissionsMixin (permissions.py) and its server tools.

Response fixtures follow Atlassian's OpenAPI definition (and were checked
against a live Jira Cloud site): the grants endpoint is not paginated and
returns ``{"permissions": [...]}`` with the permission key in ``permission``.
"""

import json
from unittest.mock import AsyncMock, MagicMock, patch
from urllib.parse import parse_qs, urlparse

import pytest

from mcp_atlassian.jira import JiraFetcher
from mcp_atlassian.jira.permissions import (
    _require_numeric_id,
    _require_permission_key,
    _require_text,
)

GROUP_ID = "ef84ff4d-03a5-475c-b09f-5cf016dd1bb0"


@pytest.fixture
def mixin(mock_config, mock_atlassian_jira):
    with patch("atlassian.Jira") as cls:
        cls.return_value = mock_atlassian_jira
        fetcher = JiraFetcher(config=mock_config)
        fetcher.jira = mock_atlassian_jira
        yield fetcher


def _grants(*entries, expand="user,group,projectRole,field,all"):
    return {"expand": expand, "permissions": list(entries)}


def _group_grant(grant_id=12420, permission="SET_ISSUE_SECURITY"):
    return {
        "id": grant_id,
        "self": f"https://x.atlassian.net/rest/api/3/permissionscheme/10019/permission/{grant_id}",
        "holder": {
            "type": "group",
            "parameter": "BB5O - bemanningsforespørsel",
            "value": GROUP_ID,
            "group": {"name": "BB5O - bemanningsforespørsel", "groupId": GROUP_ID},
            "expand": "group",
        },
        "permission": permission,
    }


def _role_grant(grant_id=12421, role_id="10003", permission="SET_ISSUE_SECURITY"):
    return {
        "id": grant_id,
        "holder": {
            "type": "projectRole",
            "parameter": role_id,
            "value": role_id,
            "projectRole": {
                "id": int(role_id),
                "name": "atlassian-addons-project-access",
            },
            "expand": "projectRole",
        },
        "permission": permission,
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


class TestValidators:
    @pytest.mark.parametrize("value", ["BB5O", "10a", "", None, "a/b", "1.5"])
    def test_numeric_id_rejects(self, value):
        with pytest.raises(ValueError, match="must be numeric"):
            _require_numeric_id("x", value)

    @pytest.mark.parametrize("value", ["10200", 10200])
    def test_numeric_id_accepts(self, value):
        assert _require_numeric_id("x", value) == "10200"

    @pytest.mark.parametrize(
        "value",
        [
            "SET_ISSUE_SECURITY",
            "com.codefortynine.deep-clone-for-jira__project-permission-single",
        ],
    )
    def test_permission_key_accepts_builtin_and_app_keys(self, value):
        assert _require_permission_key(value) == value

    @pytest.mark.parametrize("value", ["", None, "a b", "../x", "A?x=1"])
    def test_permission_key_rejects(self, value):
        with pytest.raises(ValueError, match="Invalid permission"):
            _require_permission_key(value)

    def test_text_allows_norwegian_group_names_with_spaces(self):
        name = "BB5O - bemanningsforespørsel"
        assert _require_text("x", name) == name

    @pytest.mark.parametrize("value", ["", None, "a\nb", "x" * 300])
    def test_text_rejects(self, value):
        with pytest.raises(ValueError, match="Invalid x"):
            _require_text("x", value)


class TestReads:
    def test_get_permission_scheme_for_project(self, mixin):
        mixin.jira.get.return_value = {
            "expand": "permissions,user,group,projectRole,field",
            "id": 10019,
            "self": "https://x.atlassian.net/rest/api/3/permissionscheme/10019",
            "name": "BB5O - Default software scheme",
            "description": "Bybane Oppdragsledelse",
        }
        result = mixin.get_permission_scheme_for_project("BB5O")
        assert result["scheme_id"] == "10019"
        assert result["name"] == "BB5O - Default software scheme"
        assert _queries(mixin.jira.get)[0][0] == (
            "rest/api/3/project/BB5O/permissionscheme"
        )

    def test_project_scheme_without_id_fails_loudly(self, mixin):
        mixin.jira.get.return_value = {"errorMessages": []}
        with pytest.raises(TypeError, match="no scheme id"):
            mixin.get_permission_scheme_for_project("BB5O")

    def test_grants_are_read_from_permissions_key(self, mixin):
        mixin.jira.get.return_value = _grants(_group_grant(), _role_grant())
        result = mixin.get_permission_scheme_grants("10019")
        assert result["total"] == 2
        first, second = result["grants"]
        assert first["id"] == "12420"
        assert first["permission"] == "SET_ISSUE_SECURITY"
        assert first["holder"] == {
            "type": "group",
            "parameter": "BB5O - bemanningsforespørsel",
            "value": GROUP_ID,
            "name": "BB5O - bemanningsforespørsel",
        }
        assert second["holder"]["name"] == "atlassian-addons-project-access"

    def test_grants_request_has_no_permission_param(self, mixin):
        mixin.jira.get.return_value = _grants()
        mixin.get_permission_scheme_grants("10019", "SET_ISSUE_SECURITY")
        path, query = _queries(mixin.jira.get)[0]
        assert path == "rest/api/3/permissionscheme/10019/permission"
        assert query == {"expand": ["all"]}

    def test_grants_filter_is_applied_client_side(self, mixin):
        mixin.jira.get.return_value = _grants(
            _group_grant(12420, "SET_ISSUE_SECURITY"),
            _role_grant(12249, "10003", "BROWSE_PROJECTS"),
            _role_grant(12421, "10003", "SET_ISSUE_SECURITY"),
        )
        result = mixin.get_permission_scheme_grants("10019", "SET_ISSUE_SECURITY")
        assert [g["id"] for g in result["grants"]] == ["12420", "12421"]
        assert result["total"] == 2

    def test_grants_for_permission_nobody_holds_is_empty(self, mixin):
        mixin.jira.get.return_value = _grants(
            _role_grant(1, "10003", "BROWSE_PROJECTS")
        )
        assert (
            mixin.get_permission_scheme_grants("10019", "SET_ISSUE_SECURITY")["grants"]
            == []
        )

    def test_grants_unexpected_shape_raises_instead_of_returning_empty(self, mixin):
        # This is the response shape the first implementation wrongly assumed.
        mixin.jira.get.return_value = {"values": [], "isLast": True}
        with pytest.raises(TypeError, match="'permissions'"):
            mixin.get_permission_scheme_grants("10019")

    def test_grants_without_expanded_holder_have_no_name(self, mixin):
        mixin.jira.get.return_value = _grants(
            {
                "id": 1,
                "holder": {"type": "projectLead"},
                "permission": "SET_ISSUE_SECURITY",
            }
        )
        holder = mixin.get_permission_scheme_grants("1")["grants"][0]["holder"]
        assert holder["type"] == "projectLead"
        assert holder["name"] is None

    def test_task_status_uses_documented_fields(self, mixin):
        mixin.jira.get.return_value = {
            "self": "https://x.atlassian.net/rest/api/3/task/10641",
            "id": "10641",
            "description": "d",
            "status": "COMPLETE",
            "result": {"ok": True},
            "submittedBy": 10000,
            "progress": 100,
            "elapsedRuntime": 156,
            "submitted": 1501708132800,
            "started": 1501708132900,
            "finished": 1501708133000,
            "lastUpdate": 1501708133000,
        }
        result = mixin.get_async_task("10641")
        assert result["status"] == "COMPLETE"
        assert result["progress"] == 100
        assert result["finished"] == 1501708133000
        assert _queries(mixin.jira.get)[0][0] == "rest/api/3/task/10641"

    def test_task_accepts_location_url(self, mixin):
        mixin.jira.get.return_value = {"id": "10641", "status": "RUNNING"}
        mixin.get_async_task("https://x.atlassian.net/rest/api/3/task/10641")
        assert _queries(mixin.jira.get)[0][0] == "rest/api/3/task/10641"

    def test_task_rejects_foreign_url(self, mixin):
        with pytest.raises(ValueError, match="must be numeric"):
            mixin.get_async_task("https://evil.example/other/path")
        mixin.jira.get.assert_not_called()


class TestWrites:
    def test_set_scheme_dry_run_is_default_and_makes_no_write(self, mixin):
        mixin.jira.get.side_effect = [
            {"id": "10036", "key": "BB5O"},
            {"id": 10001, "name": "Old", "description": ""},
        ]
        result = mixin.set_project_permission_scheme("BB5O", "10019")
        assert result["dry_run"] is True
        assert result["would_apply"] == {"id": 10019}
        assert result["current"]["scheme_id"] == "10001"
        mixin.jira.put.assert_not_called()

    def test_set_scheme_execute_sends_put(self, mixin):
        mixin.jira.get.return_value = {"id": "10036", "key": "BB5O"}
        mixin.jira.put.return_value = {"id": 10019, "name": "New"}
        result = mixin.set_project_permission_scheme("BB5O", "10019", dry_run=False)
        assert result["scheme_id"] == "10019"
        mixin.jira.put.assert_called_once_with(
            "rest/api/3/project/10036/permissionscheme", data={"id": 10019}
        )

    def test_grant_payload_for_group_uses_value(self, mixin):
        mixin.jira.get.return_value = _grants()
        result = mixin.set_permission_grant(
            "10019", "SET_ISSUE_SECURITY", "group", holder_value=GROUP_ID
        )
        assert result["would_apply"] == {
            "holder": {"type": "group", "value": GROUP_ID},
            "permission": "SET_ISSUE_SECURITY",
        }

    def test_grant_sends_permission_key_without_lookup(self, mixin):
        mixin.jira.get.return_value = _grants()
        mixin.set_permission_grant(
            "10019", "SET_ISSUE_SECURITY", "projectRole", "10002"
        )
        paths = [path for path, _ in _queries(mixin.jira.get)]
        assert paths == ["rest/api/3/permissionscheme/10019/permission"]

    @pytest.mark.parametrize(
        "holder_type", ["projectLead", "reporter", "assignee", "anyone"]
    )
    def test_grant_holder_types_without_identifier(self, mixin, holder_type):
        mixin.jira.get.return_value = _grants()
        result = mixin.set_permission_grant("10019", "SET_ISSUE_SECURITY", holder_type)
        assert result["would_apply"]["holder"] == {"type": holder_type}

    def test_grant_dry_run_flags_duplicate_by_value(self, mixin):
        mixin.jira.get.return_value = _grants(_group_grant(), _role_grant())
        result = mixin.set_permission_grant(
            "10019", "SET_ISSUE_SECURITY", "group", holder_value=GROUP_ID
        )
        assert result["duplicate"] is True
        assert result["existing_grant_ids"] == ["12420"]
        mixin.jira.post.assert_not_called()

    def test_grant_dry_run_flags_duplicate_by_role_id(self, mixin):
        mixin.jira.get.return_value = _grants(_role_grant())
        result = mixin.set_permission_grant(
            "10019", "SET_ISSUE_SECURITY", "projectRole", "10003"
        )
        assert result["duplicate"] is True

    def test_grant_dry_run_new_holder_is_not_duplicate(self, mixin):
        mixin.jira.get.return_value = _grants(_role_grant())
        result = mixin.set_permission_grant(
            "10019", "SET_ISSUE_SECURITY", "projectRole", "10002"
        )
        assert result["duplicate"] is False

    def test_grant_execute_posts_and_reports_grant(self, mixin):
        mixin.jira.post.return_value = {
            "id": 222,
            "permission": "SET_ISSUE_SECURITY",
            "holder": {"type": "projectRole", "parameter": "10002", "value": "10002"},
        }
        result = mixin.set_permission_grant(
            "10019", "SET_ISSUE_SECURITY", "projectRole", "10002", dry_run=False
        )
        assert result["grant_id"] == "222"
        assert result["permission"] == "SET_ISSUE_SECURITY"
        mixin.jira.post.assert_called_once_with(
            "rest/api/3/permissionscheme/10019/permission",
            data={
                "holder": {"type": "projectRole", "parameter": "10002"},
                "permission": "SET_ISSUE_SECURITY",
            },
        )

    @pytest.mark.parametrize("holder_type", ["internalUser", "role", ""])
    def test_grant_rejects_unknown_holder_type(self, mixin, holder_type):
        with pytest.raises(ValueError, match="Invalid holder_type"):
            mixin.set_permission_grant("10019", "SET_ISSUE_SECURITY", holder_type, "1")
        mixin.jira.post.assert_not_called()

    @pytest.mark.parametrize("holder_type", ["group", "user", "projectRole"])
    def test_grant_requires_identifier_for_identified_types(self, mixin, holder_type):
        with pytest.raises(ValueError, match="needs holder_parameter or holder_value"):
            mixin.set_permission_grant("10019", "SET_ISSUE_SECURITY", holder_type)

    def test_grant_rejects_bad_permission_key(self, mixin):
        with pytest.raises(ValueError, match="Invalid permission"):
            mixin.set_permission_grant("10019", "../x", "projectLead")

    def test_remove_grant_dry_run_describes_grant(self, mixin):
        mixin.jira.get.return_value = _group_grant()
        result = mixin.remove_permission_grant("10019", "12420")
        assert result["dry_run"] is True
        assert result["current"]["id"] == "12420"
        assert result["current"]["permission"] == "SET_ISSUE_SECURITY"
        assert result["current"]["holder"]["name"] == "BB5O - bemanningsforespørsel"
        mixin.jira.delete.assert_not_called()

    def test_remove_grant_execute_deletes(self, mixin):
        result = mixin.remove_permission_grant("10019", "12420", dry_run=False)
        assert result["success"] is True
        mixin.jira.delete.assert_called_once_with(
            "rest/api/3/permissionscheme/10019/permission/12420"
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
                "holder_value": GROUP_ID,
            },
        ),
        ("remove_permission_grant", {"scheme_id": "10001", "grant_id": "111"}),
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

    async def test_dry_run_tool_passes_default_to_mixin(self):
        from mcp_atlassian.servers import jira as server

        fetcher = MagicMock()
        fetcher.set_permission_grant.return_value = {
            "success": True,
            "dry_run": True,
            "action": "set_permission_grant",
        }
        with patch.object(server, "get_jira_fetcher", AsyncMock(return_value=fetcher)):
            out = await server.set_permission_grant.fn(
                _ctx(read_only=False),
                scheme_id="10001",
                permission="SET_ISSUE_SECURITY",
                holder_type="group",
                holder_value=GROUP_ID,
            )
        assert json.loads(out)["dry_run"] is True
        assert fetcher.set_permission_grant.call_args.kwargs["dry_run"] is True

    async def test_read_tool_reports_permission_error(self):
        from requests.exceptions import HTTPError

        from mcp_atlassian.servers import jira as server

        fetcher = MagicMock()
        fetcher.get_permission_scheme_grants.side_effect = HTTPError(
            "Forbidden", response=MagicMock(status_code=403)
        )
        with patch.object(server, "get_jira_fetcher", AsyncMock(return_value=fetcher)):
            out = await server.get_permission_scheme_grants.fn(
                MagicMock(), scheme_id="10001"
            )
        data = json.loads(out)
        assert data["success"] is False
        assert "Administer Jira" in data["error"]
