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


class TestDashboardOwnership:
    @pytest.fixture(autouse=True)
    def raw_requests(self, mixin):
        """Model raw responses while retaining the existing endpoint fixtures."""

        def request(method, path, *, allow_redirects, **kwargs):
            assert allow_redirects is False
            payload = getattr(mixin.jira, method.lower())(path, **kwargs)
            response = MagicMock()
            response.is_redirect = False
            response.is_permanent_redirect = False
            response.json.return_value = payload
            return response

        mixin.jira.request.side_effect = request

    def test_unsupported_adapter_rejected_before_write(self, mixin):
        from requests.adapters import BaseAdapter

        from mcp_atlassian.jira.client import Jira

        client = Jira(url="https://example.atlassian.net", cloud=True)
        client._session.mount("https://", BaseAdapter())
        mixin.jira = client
        with patch.object(
            mixin, "get_dashboard", return_value={"owner": {"accountId": "old"}}
        ):
            with patch.object(client, "request") as request:
                result = mixin.change_dashboard_owner("1", "new")
        assert result["code"] == "unsupported_transport"
        assert result["changed"] is False
        request.assert_not_called()

    @pytest.mark.parametrize("status", [200, 302, 307, 429, 503])
    def test_transport_safety_and_shared_adapter_unchanged(self, mixin, status):
        from requests import Response
        from urllib3.util.retry import Retry

        from mcp_atlassian.jira.client import Jira

        client = Jira(url="https://example.atlassian.net", cloud=True)
        mixin.jira = client
        adapter = client._session.get_adapter(client.url)
        retries = Retry(total=3, status_forcelist=[429, 503])
        adapter.max_retries = retries
        attempts = []

        def send(local_adapter, request, **kwargs):
            attempts.append(request.method)
            if request.method == "PUT" or len(attempts) > 1:
                assert local_adapter.max_retries.total == 0
                assert local_adapter.max_retries.redirect == 0
                assert local_adapter.poolmanager is adapter.poolmanager
            response = Response()
            response.status_code = 200 if len(attempts) == 1 else status
            response.headers["Location"] = "https://example.atlassian.net/redirect"
            response.headers["Retry-After"] = "0"
            response._content = json.dumps(
                {"owner": {"accountId": "old"}}
                if len(attempts) == 1
                else {"owner": {"accountId": "new"}}
                if request.method == "GET"
                else {"entityErrors": {}}
            ).encode()
            response.request = request
            response.url = request.url
            return response

        with patch(
            "requests.adapters.HTTPAdapter.send", autospec=True, side_effect=send
        ):
            result = mixin.change_dashboard_owner("1", "new")

        assert attempts == ["GET", "PUT", "GET"]
        assert adapter.max_retries is retries
        assert client._session.get_adapter(client.url) is adapter
        assert client.retry_with_header is True
        assert result["outcome"] == ("confirmed" if status < 300 else "indeterminate")

    @pytest.mark.parametrize("verification_status", [200, 429])
    def test_rate_limit_is_not_retried(self, mixin, verification_status):
        from requests import Response

        from mcp_atlassian.jira.client import Jira

        client = Jira(url="https://example.atlassian.net", cloud=True)
        mixin.jira = client
        attempts = []

        def request(method, url, **kwargs):
            attempts.append(method)
            response = Response()
            response.status_code = (
                200
                if len(attempts) == 1
                else 429
                if method == "PUT"
                else verification_status
            )
            response.headers["Retry-After"] = "0"
            response._content = json.dumps(
                {"owner": {"accountId": "old"}}
                if response.status_code == 200
                else {"errorMessages": ["SECRET"]}
            ).encode()
            return response

        with patch("requests.Session.request", side_effect=request):
            result = mixin.change_dashboard_owner("1", "new")

        assert attempts == ["GET", "PUT", "GET"]
        assert result["outcome"] == "indeterminate"
        assert result["changed"] is None
        assert "SECRET" not in json.dumps(result)
        assert client.retry_with_header is True

    def test_change_and_verify(self, mixin):
        mixin.jira.get.side_effect = [
            {"owner": {"accountId": "old"}},
            {"owner": {"accountId": "new"}},
        ]
        mixin.jira.put.return_value = {"entityErrors": {}}
        result = mixin.change_dashboard_owner("10092", "new")
        assert result["outcome"] == "confirmed"
        assert result["changed"] is True
        assert result["observed_owner_account_id"] == "new"
        mixin.jira.put.assert_called_once_with(
            "rest/api/3/dashboard/bulk/edit",
            data={
                "action": "changeOwner",
                "entityIds": [10092],
                "changeOwnerDetails": {"newOwner": "new", "autofixName": False},
                "extendAdminPermissions": False,
            },
        )
        assert mixin.jira.get.call_count == 2

    def test_precondition_before_noop(self, mixin):
        mixin.jira.get.return_value = {"owner": {"accountId": "new"}}
        result = mixin.change_dashboard_owner(
            "1", "new", expected_owner_account_id="old"
        )
        assert result["code"] == "owner_precondition_failed"
        assert result["outcome"] == "rejected"
        mixin.jira.put.assert_not_called()

    def test_noop(self, mixin):
        mixin.jira.get.return_value = {"owner": {"accountId": "new"}}
        result = mixin.change_dashboard_owner("1", "new")
        assert result["outcome"] == "confirmed"
        assert result["changed"] is False
        mixin.jira.put.assert_not_called()
        assert mixin.jira.get.call_count == 1

    @pytest.mark.parametrize(
        "after", [{"owner": {"accountId": "old"}}, {}, RuntimeError("SECRET")]
    )
    def test_verification_indeterminate(self, mixin, after):
        mixin.jira.get.side_effect = [{"owner": {"accountId": "old"}}, after]
        mixin.jira.put.return_value = {}
        result = mixin.change_dashboard_owner("1", "new")
        assert result["outcome"] == "indeterminate"
        assert result["changed"] is None
        assert "SECRET" not in json.dumps(result)
        assert mixin.jira.put.call_count == 1
        assert mixin.jira.get.call_count == 2

    def test_cloud_only(self, mixin):
        mixin.config.url = "https://jira.example.com"
        result = mixin.change_dashboard_owner("1", "new")
        assert result["code"] == "cloud_only"
        mixin.jira.get.assert_not_called()
        mixin.jira.put.assert_not_called()

    @pytest.mark.parametrize(
        "did,owner", [("x", "new"), ("1", " "), ("-1", "new"), ("١", "new")]
    )
    def test_invalid_input(self, mixin, did, owner):
        result = mixin.change_dashboard_owner(did, owner)
        assert result["code"] == "invalid_input"
        mixin.jira.get.assert_not_called()
        mixin.jira.put.assert_not_called()

    def test_preflight_failure_sanitized(self, mixin):
        mixin.jira.get.side_effect = RuntimeError("SECRET")
        result = mixin.change_dashboard_owner("1", "new")
        assert result["code"] == "dashboard_read_failed"
        assert "SECRET" not in json.dumps(result)
        mixin.jira.put.assert_not_called()

    def test_explicit_entity_rejection(self, mixin):
        mixin.jira.get.return_value = {"owner": {"accountId": "old"}}
        mixin.jira.put.return_value = {
            "entityErrors": {"1": {"errorMessages": ["SECRET"]}}
        }
        result = mixin.change_dashboard_owner("1", "new")
        assert result["code"] == "jira_rejected"
        assert result["outcome"] == "rejected"
        assert "SECRET" not in json.dumps(result)
        assert mixin.jira.get.call_count == 2

    def test_transport_failure_still_verified(self, mixin):
        mixin.jira.get.side_effect = [
            {"owner": {"accountId": "old"}},
            {"owner": {"accountId": "new"}},
        ]
        mixin.jira.put.side_effect = RuntimeError("SECRET")
        result = mixin.change_dashboard_owner("1", "new")
        assert result["outcome"] == "confirmed"
        assert mixin.jira.put.call_count == 1
        assert mixin.jira.get.call_count == 2

    @pytest.mark.parametrize(
        "status,outcome",
        [
            (400, "rejected"),
            (403, "rejected"),
            (500, "indeterminate"),
            (429, "indeterminate"),
        ],
    )
    def test_http_failure_sanitized(self, mixin, status, outcome):
        from requests import Response
        from requests.exceptions import HTTPError

        response = Response()
        response.status_code = status
        mixin.jira.put.side_effect = HTTPError("SECRET", response=response)
        mixin.jira.get.return_value = {"owner": {"accountId": "old"}}
        result = mixin.change_dashboard_owner("1", "new")
        assert result["outcome"] == outcome
        assert result["http_status"] == status
        assert "SECRET" not in json.dumps(result)
        assert mixin.jira.get.call_count == 2
        assert mixin.jira.put.call_count == 1

    def test_opt_in_flags_and_missing_owner(self, mixin):
        mixin.jira.get.side_effect = [
            {"owner": {"accountId": "old"}},
            {"owner": {"accountId": "new"}},
        ]
        mixin.change_dashboard_owner(
            "1",
            "new",
            use_admin_permissions=True,
            autofix_name=True,
            expected_owner_account_id="old",
        )
        payload = mixin.jira.put.call_args.kwargs["data"]
        assert payload["extendAdminPermissions"] is True
        assert payload["changeOwnerDetails"]["autofixName"] is True
        mixin.jira.get.side_effect = None
        mixin.jira.get.return_value = {}
        mixin.jira.put.reset_mock()
        assert mixin.change_dashboard_owner("1", "new")["code"] == "owner_unavailable"
        mixin.jira.put.assert_not_called()

    def test_get_myself_allowlist(self, mixin):
        mixin.jira.myself.return_value = {
            "accountId": "me",
            "displayName": "Me",
            "active": True,
            "emailAddress": "private",
            "secret": "SECRET",
            "key": "key",
        }
        assert mixin.get_myself() == {
            "accountId": "me",
            "displayName": "Me",
            "active": True,
            "key": "key",
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
    ("change_dashboard_owner", {"dashboard_id": "1", "new_owner_account_id": "new"}),
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

    async def test_ownership_result_and_parameters(self):
        from mcp_atlassian.servers import jira as server

        fetcher = MagicMock()
        fetcher.change_dashboard_owner.return_value = {
            "success": False,
            "outcome": "indeterminate",
            "changed": None,
        }
        with patch.object(server, "get_jira_fetcher", AsyncMock(return_value=fetcher)):
            result = json.loads(
                await server.change_dashboard_owner.fn(
                    _ctx(read_only=False), "1", "new", expected_owner_account_id="old"
                )
            )
        assert result == fetcher.change_dashboard_owner.return_value
        fetcher.change_dashboard_owner.assert_called_once_with(
            "1",
            "new",
            use_admin_permissions=False,
            autofix_name=False,
            expected_owner_account_id="old",
        )

    @pytest.mark.parametrize("tool", ["get_myself", "change_dashboard_owner"])
    async def test_fetcher_initialization_error_is_sanitized(self, tool):
        from mcp_atlassian.servers import jira as server

        with patch.object(
            server, "get_jira_fetcher", AsyncMock(side_effect=ValueError("SECRET"))
        ):
            args = ("1", "new") if tool == "change_dashboard_owner" else ()
            result = json.loads(
                await getattr(server, tool).fn(_ctx(read_only=False), *args)
            )
        assert result["success"] is False
        assert "SECRET" not in json.dumps(result)
        if tool == "get_myself":
            assert result["code"] == "identity_read_failed"
        else:
            assert result["code"] == "client_initialization_failed"
            assert result["outcome"] == "rejected"
            assert result["changed"] is False
            assert "no ownership write was attempted" in result["message"]

    async def test_myself_and_sanitized_error(self):
        from mcp_atlassian.servers import jira as server

        fetcher = MagicMock()
        fetcher.get_myself.return_value = {"accountId": "me"}
        with patch.object(server, "get_jira_fetcher", AsyncMock(return_value=fetcher)):
            result = json.loads(await server.get_myself.fn(_ctx(read_only=True)))
            assert result == {"success": True, "user": {"accountId": "me"}}
            fetcher.get_myself.side_effect = RuntimeError("SECRET")
            result = json.loads(await server.get_myself.fn(_ctx(read_only=True)))
            assert result["code"] == "identity_read_failed"
            assert "SECRET" not in json.dumps(result)

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
