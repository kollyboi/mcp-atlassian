"""Tests for OrgAdminMixin (org_admin.py)."""

import os
from unittest.mock import MagicMock, patch

import pytest
import requests

from mcp_atlassian.jira import JiraFetcher
from mcp_atlassian.jira.config import JiraConfig

BASE = "https://api.atlassian.com/admin"
ORG = "test-org-id"
DIR = "test-dir-id"
CREDS_ENV = {"JIRA_ORG_ID": ORG, "JIRA_ORG_KEY": "test-key"}


@pytest.fixture
def mixin(mock_config, mock_atlassian_jira):
    with patch("atlassian.Jira") as cls:
        cls.return_value = mock_atlassian_jira
        fetcher = JiraFetcher(config=mock_config)
        fetcher.jira = mock_atlassian_jira
        yield fetcher


def _mock_response(json_data, status_code=200):
    r = MagicMock()
    r.status_code = status_code
    r.json.return_value = json_data
    r.raise_for_status = MagicMock()
    if status_code >= 400:
        r.raise_for_status.side_effect = requests.HTTPError(response=r)
    return r


def _user(account_id="u1", account_type="atlassian", status="active"):
    return {
        "accountId": account_id,
        "accountType": account_type,
        "name": "Test User",
        "nickname": None,
        "email": "test@example.com",
        "status": status,
        "accountStatus": "active",
        "membershipStatus": "active",
        "claimStatus": "managed",
        "emailVerified": True,
        "mfaEnabled": False,
        "addedToOrg": "2024-01-01T00:00:00.000Z",
        "platformRoles": [],
        "managementSource": "invited",
        "picture": None,
        "avatar": None,
        "jobTitle": None,
        "department": None,
        "organization": None,
        "location": None,
        "timeZone": None,
        "counts": None,
    }


class TestCredentialValidation:
    def test_missing_org_id_raises(self, mixin):
        with patch.dict(os.environ, {}, clear=True):
            with pytest.raises(ValueError, match="JIRA_ORG_ID"):
                mixin._get_org_credentials()

    def test_missing_key_raises(self, mixin):
        with patch.dict(os.environ, {"JIRA_ORG_ID": ORG}, clear=True):
            with pytest.raises(ValueError, match="JIRA_ORG_KEY"):
                mixin._get_org_credentials()

    def test_fallback_to_admin_token(self, mixin):
        env = {"JIRA_ORG_ID": ORG, "JIRA_ADMIN_TOKEN": "admin-tok"}
        with patch.dict(os.environ, env, clear=True):
            org_id, key = mixin._get_org_credentials()
        assert org_id == ORG
        assert key == "admin-tok"


class TestDirIdHelper:
    def test_none_returns_wildcard(self, mixin):
        assert mixin._dir_id(None) == "-"

    def test_explicit_id_returned(self, mixin):
        assert mixin._dir_id("abc") == "abc"


class TestPaginatedGet:
    def test_single_page(self, mixin):
        resp = _mock_response({"data": [{"id": 1}], "links": {}})
        with patch.dict(os.environ, CREDS_ENV):
            with patch("requests.get", return_value=resp) as mock_get:
                result = mixin._paginated_get("http://test", {}, {"limit": 10})
        assert result == [{"id": 1}]
        mock_get.assert_called_once()

    def test_two_pages(self, mixin):
        resp1 = _mock_response({"data": [{"id": 1}], "links": {"next": "cur2"}})
        resp2 = _mock_response({"data": [{"id": 2}], "links": {}})
        with patch("requests.get", side_effect=[resp1, resp2]) as mock_get:
            result = mixin._paginated_get("http://test", {}, {"limit": 10})
        assert len(result) == 2
        assert mock_get.call_count == 2
        # Second call uses only cursor param
        _, kwargs2 = mock_get.call_args_list[1]
        assert kwargs2["params"] == {"cursor": "cur2"}


class TestFetchOrgUsersV2:
    def test_basic_fetch(self, mixin):
        page = {"data": [_user("u1")], "links": {}}
        with patch.dict(os.environ, CREDS_ENV):
            with patch("requests.get", return_value=_mock_response(page)):
                users = mixin.fetch_org_users_v2()
        assert len(users) == 1
        assert users[0]["accountId"] == "u1"
        assert users[0]["accountType"] == "atlassian"

    def test_status_list_passed_as_array(self, mixin):
        page = {"data": [], "links": {}}
        with patch.dict(os.environ, CREDS_ENV):
            with patch("requests.get", return_value=_mock_response(page)) as mock_get:
                mixin.fetch_org_users_v2(status_filter=["active", "suspended"])
        params = mock_get.call_args[1]["params"]
        assert params["status"] == ["active", "suspended"]

    def test_status_string_wrapped_in_list(self, mixin):
        page = {"data": [], "links": {}}
        with patch.dict(os.environ, CREDS_ENV):
            with patch("requests.get", return_value=_mock_response(page)) as mock_get:
                mixin.fetch_org_users_v2(status_filter="active")
        params = mock_get.call_args[1]["params"]
        assert params["status"] == ["active"]

    def test_wildcard_directory_used_by_default(self, mixin):
        page = {"data": [], "links": {}}
        with patch.dict(os.environ, CREDS_ENV):
            with patch("requests.get", return_value=_mock_response(page)) as mock_get:
                mixin.fetch_org_users_v2()
        url = mock_get.call_args[0][0]
        assert f"/directories/-/users" in url

    def test_search_term_and_mfa_passed(self, mixin):
        page = {"data": [], "links": {}}
        with patch.dict(os.environ, CREDS_ENV):
            with patch("requests.get", return_value=_mock_response(page)) as mock_get:
                mixin.fetch_org_users_v2(search_term="alice", mfa_enabled=True)
        params = mock_get.call_args[1]["params"]
        assert params["searchTerm"] == "alice"
        assert params["mfaEnabled"] is True

    def test_group_ids_and_email_domains(self, mixin):
        page = {"data": [], "links": {}}
        with patch.dict(os.environ, CREDS_ENV):
            with patch("requests.get", return_value=_mock_response(page)) as mock_get:
                mixin.fetch_org_users_v2(
                    group_ids=["g1", "g2"],
                    email_domains=["example.com"],
                )
        params = mock_get.call_args[1]["params"]
        assert params["groupIds"] == ["g1", "g2"]
        assert params["emailDomains"] == ["example.com"]

    def test_all_v2_fields_captured(self, mixin):
        raw = {
            "accountId": "u1",
            "accountType": "atlassian",
            "name": "Alice",
            "nickname": "al",
            "email": "alice@x.com",
            "status": "active",
            "accountStatus": "active",
            "membershipStatus": "active",
            "claimStatus": "managed",
            "emailVerified": True,
            "mfaEnabled": True,
            "addedToOrg": "2024-01-01T00:00:00Z",
            "platformRoles": [{"roleId": "atlassian/admin"}],
            "managementSource": "synced",
            "picture": "https://pic.test",
            "avatar": "https://av.test",
            "jobTitle": "Engineer",
            "department": "Eng",
            "organization": "Corp",
            "location": "Oslo",
            "timeZone": "Europe/Oslo",
            "counts": {"resources": 5},
        }
        page = {"data": [raw], "links": {}}
        with patch.dict(os.environ, CREDS_ENV):
            with patch("requests.get", return_value=_mock_response(page)):
                users = mixin.fetch_org_users_v2()
        u = users[0]
        assert u["nickname"] == "al"
        assert u["platformRoles"] == [{"roleId": "atlassian/admin"}]
        assert u["managementSource"] == "synced"
        assert u["organization"] == "Corp"
        assert u["location"] == "Oslo"
        assert u["timeZone"] == "Europe/Oslo"
        assert u["counts"] == {"resources": 5}


class TestCountOrgUsers:
    def test_returns_count(self, mixin):
        with patch.dict(os.environ, CREDS_ENV):
            with patch("requests.get", return_value=_mock_response({"count": 42})):
                count = mixin.count_org_users()
        assert count == 42

    def test_url_contains_count_endpoint(self, mixin):
        with patch.dict(os.environ, CREDS_ENV):
            with patch("requests.get", return_value=_mock_response({"count": 0})) as mock_get:
                mixin.count_org_users(status_filter="active")
        url = mock_get.call_args[0][0]
        assert url.endswith("/users/count")

    def test_no_limit_in_params(self, mixin):
        with patch.dict(os.environ, CREDS_ENV):
            with patch("requests.get", return_value=_mock_response({"count": 0})) as mock_get:
                mixin.count_org_users()
        params = mock_get.call_args[1]["params"]
        assert "limit" not in params


class TestGetOrgUserDetails:
    def test_returns_user(self, mixin):
        raw = _user("u99")
        with patch.dict(os.environ, CREDS_ENV):
            with patch("requests.get", return_value=_mock_response({"data": raw})):
                user = mixin.get_org_user_details("u99")
        assert user["accountId"] == "u99"

    def test_url_contains_account_id(self, mixin):
        raw = _user("u99")
        with patch.dict(os.environ, CREDS_ENV):
            with patch("requests.get", return_value=_mock_response({"data": raw})) as mock_get:
                mixin.get_org_user_details("u99")
        url = mock_get.call_args[0][0]
        assert "u99" in url

    def test_404_raises(self, mixin):
        with patch.dict(os.environ, CREDS_ENV):
            with patch("requests.get", return_value=_mock_response({}, 404)):
                with pytest.raises(requests.HTTPError):
                    mixin.get_org_user_details("nobody")


class TestGetOrgUserRoleAssignments:
    def test_returns_assignments(self, mixin):
        assignment = {"resourceId": "ari:cloud:jira::site/1", "roles": ["atlassian/admin"]}
        page = {"data": [assignment], "links": {}}
        with patch.dict(os.environ, CREDS_ENV):
            with patch("requests.get", return_value=_mock_response(page)):
                result = mixin.get_org_user_role_assignments("u1")
        assert result == [assignment]

    def test_filters_passed(self, mixin):
        page = {"data": [], "links": {}}
        with patch.dict(os.environ, CREDS_ENV):
            with patch("requests.get", return_value=_mock_response(page)) as mock_get:
                mixin.get_org_user_role_assignments(
                    "u1",
                    resource_ids=["ari:cloud:jira::site/1"],
                    role_ids=["atlassian/admin"],
                )
        params = mock_get.call_args[1]["params"]
        assert params["resourceIds"] == ["ari:cloud:jira::site/1"]
        assert params["roleIds"] == ["atlassian/admin"]


class TestGetOrgUserStatistics:
    def test_uses_stats_endpoint(self, mixin):
        dirs_resp = _mock_response({"data": [{"directoryId": DIR}]})
        stats_resp = _mock_response({
            "accountStatus": [
                {"status": "active", "count": 10},
                {"status": "inactive", "count": 2},
            ]
        })
        with patch.dict(os.environ, CREDS_ENV):
            with patch("requests.get", side_effect=[dirs_resp, stats_resp]):
                result = mixin.get_org_user_statistics()
        assert result["total_users"] == 12
        assert result["active_members"] == 10
        assert result["by_status"]["inactive"] == 2

    def test_fallback_on_404(self, mixin):
        dirs_resp = _mock_response({"data": [{"directoryId": DIR}]})
        stats_404 = _mock_response({}, 404)
        users_resp = _mock_response({"data": [
            {**_user("u1"), "status": "active", "claimStatus": "managed"},
            {**_user("u2", status="suspended"), "status": "suspended", "claimStatus": "unmanaged"},
        ], "links": {}})
        with patch.dict(os.environ, CREDS_ENV):
            with patch("requests.get", side_effect=[dirs_resp, stats_404, users_resp]):
                result = mixin.get_org_user_statistics()
        assert result["total_users"] == 2
        assert result["by_status"]["active"] == 1
        assert result["by_status"]["suspended"] == 1
        assert result["by_claim_status"]["managed"] == 1


class TestSuspendReactivate:
    def test_suspend_uses_wildcard_dir(self, mixin):
        resp = _mock_response({}, 204)
        with patch.dict(os.environ, CREDS_ENV):
            with patch("requests.post", return_value=resp) as mock_post:
                mixin.suspend_org_user("u1")
        url = mock_post.call_args[0][0]
        assert "/directories/-/users/u1/suspend" in url

    def test_reactivate_uses_wildcard_dir(self, mixin):
        resp = _mock_response({}, 204)
        with patch.dict(os.environ, CREDS_ENV):
            with patch("requests.post", return_value=resp) as mock_post:
                mixin.reactivate_org_user("u1")
        url = mock_post.call_args[0][0]
        assert "/directories/-/users/u1/restore" in url

    def test_explicit_directory_id(self, mixin):
        resp = _mock_response({}, 204)
        with patch.dict(os.environ, CREDS_ENV):
            with patch("requests.post", return_value=resp) as mock_post:
                mixin.suspend_org_user("u1", directory_id="my-dir")
        url = mock_post.call_args[0][0]
        assert "/directories/my-dir/users/u1/suspend" in url


class TestRemoveOrgUser:
    def test_delete_called(self, mixin):
        resp = _mock_response({}, 204)
        with patch.dict(os.environ, CREDS_ENV):
            with patch("requests.delete", return_value=resp) as mock_del:
                result = mixin.remove_org_user_from_directory("u1")
        assert result is True
        url = mock_del.call_args[0][0]
        assert "/directories/-/users/u1" in url

    def test_404_raises(self, mixin):
        with patch.dict(os.environ, CREDS_ENV):
            with patch("requests.delete", return_value=_mock_response({}, 404)):
                with pytest.raises(requests.HTTPError):
                    mixin.remove_org_user_from_directory("ghost")


class TestGetInactiveUsers:
    def _v1_user(self, account_id, account_type="atlassian", last_ts="2025-01-01T00:00:00Z"):
        return {
            "accountId": account_id,
            "accountType": account_type,
            "accountStatus": "active",
            "statusInUserbase": True,
            "name": "Test",
            "email": "t@t.com",
            "groups": [],
            "productLastAccess": [
                {"productKey": "jira-software", "lastActiveTimestamp": last_ts}
            ],
        }

    def test_returns_inactive_human_users(self, mixin):
        v2_page = {"data": [_user("u1", "atlassian")], "links": {}}
        v1_data = {"data": [self._v1_user("u1", last_ts="2020-01-01T00:00:00Z")], "links": {}}

        with patch.dict(os.environ, CREDS_ENV):
            with patch("requests.get", return_value=_mock_response(v2_page)):
                with patch("requests.post", return_value=_mock_response(v1_data)):
                    result = mixin.get_inactive_users(days=30)

        assert len(result) == 1
        assert result[0]["accountId"] == "u1"
        assert result[0]["_days_inactive"] >= 30

    def test_filters_non_atlassian_account_type(self, mixin):
        v2_page = {"data": [_user("u1", "customer")], "links": {}}
        v1_data = {"data": [self._v1_user("u1", "customer", "2020-01-01T00:00:00Z")], "links": {}}

        with patch.dict(os.environ, CREDS_ENV):
            with patch("requests.get", return_value=_mock_response(v2_page)):
                with patch("requests.post", return_value=_mock_response(v1_data)):
                    result = mixin.get_inactive_users(days=30)

        assert result == []

    def test_excludes_account_ids(self, mixin):
        v2_page = {"data": [_user("u1"), _user("u2")], "links": {}}
        v1_data = {"data": [
            self._v1_user("u1", last_ts="2020-01-01T00:00:00Z"),
            self._v1_user("u2", last_ts="2020-01-01T00:00:00Z"),
        ], "links": {}}

        with patch.dict(os.environ, CREDS_ENV):
            with patch("requests.get", return_value=_mock_response(v2_page)):
                with patch("requests.post", return_value=_mock_response(v1_data)):
                    result = mixin.get_inactive_users(days=30, exclude_account_ids=["u1"])

        assert len(result) == 1
        assert result[0]["accountId"] == "u2"

    def test_include_no_activity(self, mixin):
        v2_page = {"data": [_user("u1")], "links": {}}
        v1_data = {"data": [{
            "accountId": "u1",
            "accountType": "atlassian",
            "accountStatus": "active",
            "statusInUserbase": True,
            "name": "Test",
            "email": "t@t.com",
            "groups": [],
            "productLastAccess": [],  # no activity
        }], "links": {}}

        with patch.dict(os.environ, CREDS_ENV):
            with patch("requests.get", return_value=_mock_response(v2_page)):
                with patch("requests.post", return_value=_mock_response(v1_data)):
                    result = mixin.get_inactive_users(days=30, include_no_activity=True)

        assert len(result) == 1
        assert result[0]["_last_access"] is None

    def test_uses_v2_account_type_not_v1(self, mixin):
        """v2 accountType drives the human filter, not v1."""
        v2_page = {"data": [_user("u1", account_type="atlassian")], "links": {}}
        v1_data = {"data": [{
            "accountId": "u1",
            "accountType": "customer",  # v1 says customer — should be ignored
            "accountStatus": "active",
            "statusInUserbase": True,
            "name": "Test",
            "email": "t@t.com",
            "groups": [],
            "productLastAccess": [{"productKey": "jira-software", "lastActiveTimestamp": "2020-01-01T00:00:00Z"}],
        }], "links": {}}

        with patch.dict(os.environ, CREDS_ENV):
            with patch("requests.get", return_value=_mock_response(v2_page)):
                with patch("requests.post", return_value=_mock_response(v1_data)):
                    result = mixin.get_inactive_users(days=30)

        assert len(result) == 1
