"""Module for Atlassian Organization Admin API operations.

Uses the Atlassian Admin API (api.atlassian.com/admin/...).

Two API versions are used for different purposes:

  v2 (GET /admin/v2/orgs/{orgId}/directories/-/users)
    - Has a composite `status` field that exactly matches the admin UI:
        status=active  →  accountStatus=active AND membershipStatus=active
        status=suspended  →  accountStatus=active AND membershipStatus=suspended
        status=not_invited  →  accountStatus=active AND membershipStatus=no_membership
        status=deactivated  →  accountStatus=inactive
    - directoryId='-' addresses all directories the admin can manage.
    - Does NOT include productLastAccess.
    - Used for: listing users, filtering, statistics, lifecycle actions.

  v1 (POST /admin/v1/orgs/{orgId}/users/search, deprecated June 2026)
    - Returns productLastAccess data (login timestamps per product).
    - `accountStatus=active` is broader than the UI's "Active": it includes users
      with suspended directory membership (membershipStatus=suspended).
    - Used for: inactivity analysis (cross-referenced against v2 active IDs).

Credentials required (separate from regular Jira credentials):
  JIRA_ORG_ID      — Atlassian organization ID
  JIRA_ORG_KEY     — Organization-level admin API key (or use JIRA_ADMIN_TOKEN)
"""

import logging
import os
from datetime import UTC, datetime
from typing import Any

import requests

from .client import JiraClient

logger = logging.getLogger("mcp-jira")

ATLASSIAN_ADMIN_BASE_URL = "https://api.atlassian.com"
_WILDCARD_DIR = "-"


class OrgAdminMixin(JiraClient):
    """Mixin for Atlassian Organization Admin API operations."""

    def _get_org_credentials(self) -> tuple[str, str]:
        org_id = os.getenv("JIRA_ORG_ID")
        org_key = os.getenv("JIRA_ORG_KEY") or os.getenv("JIRA_ADMIN_TOKEN")
        if not org_id or not org_key:
            raise ValueError(
                "JIRA_ORG_ID and JIRA_ORG_KEY (or JIRA_ADMIN_TOKEN) environment variables "
                "are required for organization admin operations."
            )
        return org_id, org_key

    def _org_admin_headers(self, org_key: str) -> dict[str, str]:
        return {
            "Authorization": f"Bearer {org_key}",
            "Accept": "application/json",
            "Content-Type": "application/json",
        }

    @staticmethod
    def _dir_id(directory_id: str | None) -> str:
        """Return directory_id or the wildcard '-' that covers all directories."""
        return directory_id or _WILDCARD_DIR

    def _paginated_get(
        self,
        url: str,
        headers: dict[str, str],
        params: dict[str, Any],
    ) -> list[dict]:
        """GET all pages from a cursor-paginated v2 endpoint.

        Mutates *params* to add `cursor` on subsequent requests.
        Returns the concatenated `data` arrays.
        """
        all_items: list[dict] = []
        while True:
            response = requests.get(url, headers=headers, params=params)
            response.raise_for_status()
            body = response.json()
            all_items.extend(body.get("data", []))
            cursor = body.get("links", {}).get("next")
            if not cursor:
                break
            params = {"cursor": cursor}
        return all_items

    def get_org_directory_id(self) -> str:
        """Return the primary directory ID for this organization (v2 directories endpoint)."""
        org_id, org_key = self._get_org_credentials()
        url = f"{ATLASSIAN_ADMIN_BASE_URL}/admin/v2/orgs/{org_id}/directories"
        response = requests.get(url, headers=self._org_admin_headers(org_key))
        response.raise_for_status()
        dirs = response.json().get("data", [])
        if not dirs:
            raise ValueError("No directories found in organization.")
        return dirs[0]["directoryId"]

    # ------------------------------------------------------------------
    # v2 — accurate status, no productLastAccess
    # ------------------------------------------------------------------

    @staticmethod
    def _build_user_params(
        *,
        limit: int = 100,
        status_filter: str | list[str] | None = None,
        claim_status: str | None = None,
        account_status: str | list[str] | None = None,
        membership_status: str | list[str] | None = None,
        account_ids: list[str] | None = None,
        directory_ids: list[str] | None = None,
        group_ids: list[str] | None = None,
        role_ids: list[str] | None = None,
        email_domains: list[str] | None = None,
        search_term: str | None = None,
        mfa_enabled: bool | None = None,
    ) -> dict[str, Any]:
        """Build query-param dict for v2 users endpoints."""
        params: dict[str, Any] = {"limit": limit}
        if status_filter:
            params["status"] = status_filter if isinstance(status_filter, list) else [status_filter]
        if claim_status:
            params["claimStatus"] = claim_status
        if account_status:
            params["accountStatus"] = account_status if isinstance(account_status, list) else [account_status]
        if membership_status:
            params["membershipStatus"] = membership_status if isinstance(membership_status, list) else [membership_status]
        if account_ids:
            params["accountIds"] = account_ids
        if directory_ids:
            params["directoryIds"] = directory_ids
        if group_ids:
            params["groupIds"] = group_ids
        if role_ids:
            params["roleIds"] = role_ids
        if email_domains:
            params["emailDomains"] = email_domains
        if search_term:
            params["searchTerm"] = search_term
        if mfa_enabled is not None:
            params["mfaEnabled"] = mfa_enabled
        return params

    @staticmethod
    def _parse_user(raw: dict) -> dict[str, Any]:
        """Extract all MultiDirectoryUser fields from a raw v2 response item."""
        return {
            "accountId": raw.get("accountId"),
            "accountType": raw.get("accountType"),
            "name": raw.get("name", ""),
            "nickname": raw.get("nickname"),
            "email": raw.get("email", ""),
            "status": raw.get("status"),
            "accountStatus": raw.get("accountStatus"),
            "membershipStatus": raw.get("membershipStatus"),
            "claimStatus": raw.get("claimStatus"),
            "emailVerified": raw.get("emailVerified"),
            "mfaEnabled": raw.get("mfaEnabled"),
            "addedToOrg": raw.get("addedToOrg"),
            "platformRoles": raw.get("platformRoles"),
            "managementSource": raw.get("managementSource"),
            "picture": raw.get("picture"),
            "avatar": raw.get("avatar"),
            "jobTitle": raw.get("jobTitle"),
            "department": raw.get("department"),
            "organization": raw.get("organization"),
            "location": raw.get("location"),
            "timeZone": raw.get("timeZone"),
            "counts": raw.get("counts"),
        }

    def fetch_org_users_v2(
        self,
        status_filter: str | list[str] | None = None,
        claim_status: str | None = None,
        account_status: str | list[str] | None = None,
        membership_status: str | list[str] | None = None,
        account_ids: list[str] | None = None,
        directory_ids: list[str] | None = None,
        group_ids: list[str] | None = None,
        role_ids: list[str] | None = None,
        email_domains: list[str] | None = None,
        search_term: str | None = None,
        mfa_enabled: bool | None = None,
        directory_id: str | None = None,
        limit: int = 100,
    ) -> list[dict[str, Any]]:
        """Fetch users via the v2 API with rich filtering.

        The v2 `status` field matches the admin UI exactly:
          'active'      = accountStatus=active AND membershipStatus=active
          'suspended'   = accountStatus=active AND membershipStatus=suspended
          'not_invited' = accountStatus=active AND membershipStatus=no_membership
          'deactivated' = accountStatus=inactive
          'for_deletion'= managed account scheduled for deletion

        Args:
            status_filter: One or more of 'active', 'suspended', 'not_invited',
                'deactivated', 'for_deletion' (string or list). None = all.
            claim_status: 'managed' or 'unmanaged', or None.
            account_status: One or more of 'active', 'inactive', 'closed'.
            membership_status: One or more of 'active', 'suspended', 'no_membership'.
            account_ids: Filter to at most 10 specific account IDs.
            directory_ids: Filter to specific directory IDs (max 10).
            group_ids: Filter to members of specific groups (max 10).
            role_ids: Filter by platform role IDs (max 10).
            email_domains: Filter by email domain (max 10).
            search_term: Search nickname and email fields.
            mfa_enabled: If True/False, filter by two-step verification status.
            directory_id: Target directory ID. None uses wildcard '-' (all directories).
            limit: Page size (max 100).

        Returns:
            List of user dicts with all MultiDirectoryUser fields.
        """
        org_id, org_key = self._get_org_credentials()
        dir_id = self._dir_id(directory_id)
        url = f"{ATLASSIAN_ADMIN_BASE_URL}/admin/v2/orgs/{org_id}/directories/{dir_id}/users"
        headers = self._org_admin_headers(org_key)
        params = self._build_user_params(
            limit=limit,
            status_filter=status_filter,
            claim_status=claim_status,
            account_status=account_status,
            membership_status=membership_status,
            account_ids=account_ids,
            directory_ids=directory_ids,
            group_ids=group_ids,
            role_ids=role_ids,
            email_domains=email_domains,
            search_term=search_term,
            mfa_enabled=mfa_enabled,
        )
        return [self._parse_user(raw) for raw in self._paginated_get(url, headers, params)]

    def get_org_user_details(
        self,
        account_id: str,
        directory_id: str | None = None,
    ) -> dict[str, Any]:
        """Fetch detailed information about a single user (v2 API).

        Args:
            account_id: The user's Atlassian account ID.
            directory_id: Directory to query. None uses wildcard '-'.

        Returns:
            MultiDirectoryUser dict for the user.
        """
        org_id, org_key = self._get_org_credentials()
        dir_id = self._dir_id(directory_id)
        url = (
            f"{ATLASSIAN_ADMIN_BASE_URL}/admin/v2/orgs/{org_id}"
            f"/directories/{dir_id}/users/{account_id}"
        )
        response = requests.get(url, headers=self._org_admin_headers(org_key))
        response.raise_for_status()
        raw = response.json().get("data", {})
        return self._parse_user(raw)

    def count_org_users(
        self,
        status_filter: str | list[str] | None = None,
        claim_status: str | None = None,
        account_status: str | list[str] | None = None,
        membership_status: str | list[str] | None = None,
        account_ids: list[str] | None = None,
        directory_ids: list[str] | None = None,
        group_ids: list[str] | None = None,
        role_ids: list[str] | None = None,
        email_domains: list[str] | None = None,
        search_term: str | None = None,
        mfa_enabled: bool | None = None,
        directory_id: str | None = None,
    ) -> int:
        """Return the count of users matching the given filters (v2 count endpoint).

        Accepts the same filter arguments as fetch_org_users_v2 (except limit/sort).
        Much cheaper than fetching all pages when only a count is needed.
        """
        org_id, org_key = self._get_org_credentials()
        dir_id = self._dir_id(directory_id)
        url = (
            f"{ATLASSIAN_ADMIN_BASE_URL}/admin/v2/orgs/{org_id}"
            f"/directories/{dir_id}/users/count"
        )
        params = self._build_user_params(
            status_filter=status_filter,
            claim_status=claim_status,
            account_status=account_status,
            membership_status=membership_status,
            account_ids=account_ids,
            directory_ids=directory_ids,
            group_ids=group_ids,
            role_ids=role_ids,
            email_domains=email_domains,
            search_term=search_term,
            mfa_enabled=mfa_enabled,
        )
        params.pop("limit", None)
        response = requests.get(url, headers=self._org_admin_headers(org_key), params=params)
        response.raise_for_status()
        return response.json().get("count", 0)

    def get_org_user_statistics(self) -> dict[str, Any]:
        """Return user counts matching the admin UI breakdowns.

        Calls the v2 stats endpoint first; falls back to client-side aggregation
        via fetch_org_users_v2() for older tenants that return 404.

        Returns:
            Dict with total_users, active_members, by_status, by_claim_status,
            and status_legend.
        """
        org_id, org_key = self._get_org_credentials()
        dir_id = self.get_org_directory_id()
        url = (
            f"{ATLASSIAN_ADMIN_BASE_URL}/admin/v2/orgs/{org_id}"
            f"/directories/{dir_id}/users/stats"
        )
        headers = self._org_admin_headers(org_key)
        response = requests.get(url, headers=headers)

        if response.status_code == 404:
            return self._get_org_user_statistics_fallback()

        response.raise_for_status()
        stats_data = response.json()

        by_status: dict[str, int] = {
            item["status"]: item["count"]
            for item in stats_data.get("accountStatus", [])
        }
        total = sum(by_status.values())

        return {
            "total_users": total,
            "active_members": by_status.get("active", 0),
            "by_status": by_status,
            "by_claim_status": {},
            "status_legend": _STATUS_LEGEND,
        }

    def _get_org_user_statistics_fallback(self) -> dict[str, Any]:
        """Client-side stats aggregation for tenants without the stats endpoint."""
        users = self.fetch_org_users_v2()
        by_status: dict[str, int] = {}
        by_claim: dict[str, int] = {}

        for u in users:
            s = u["status"] or "unknown"
            by_status[s] = by_status.get(s, 0) + 1
            c = u["claimStatus"] or "unknown"
            by_claim[c] = by_claim.get(c, 0) + 1

        return {
            "total_users": len(users),
            "active_members": by_status.get("active", 0),
            "by_status": by_status,
            "by_claim_status": by_claim,
            "status_legend": _STATUS_LEGEND,
        }

    def get_org_user_role_assignments(
        self,
        account_id: str,
        directory_id: str | None = None,
        resource_ids: list[str] | None = None,
        role_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Return all role assignments for a user (v2 API), paginated.

        Args:
            account_id: The user's Atlassian account ID.
            directory_id: Directory to query. None uses wildcard '-'.
            resource_ids: Filter by Atlassian Resource Identifier (ARI) strings.
            role_ids: Filter by canonical role IDs (e.g. 'atlassian/admin').

        Returns:
            List of MultiDirectoryUserRoleAssignment dicts.
        """
        org_id, org_key = self._get_org_credentials()
        dir_id = self._dir_id(directory_id)
        url = (
            f"{ATLASSIAN_ADMIN_BASE_URL}/admin/v2/orgs/{org_id}"
            f"/directories/{dir_id}/users/{account_id}/role-assignments"
        )
        headers = self._org_admin_headers(org_key)
        params: dict[str, Any] = {"limit": 100}
        if resource_ids:
            params["resourceIds"] = resource_ids
        if role_ids:
            params["roleIds"] = role_ids
        return self._paginated_get(url, headers, params)

    # ------------------------------------------------------------------
    # v1 — has productLastAccess, but broader "active" definition
    # ------------------------------------------------------------------

    def _fetch_org_users_v1_with_product_access(self) -> list[dict[str, Any]]:
        """Fetch all users via v1 with productLastAccess expansion.

        Returns all accounts regardless of membershipStatus — callers are
        responsible for cross-referencing against v2 active IDs if needed.
        """
        org_id, org_key = self._get_org_credentials()
        headers = self._org_admin_headers(org_key)
        endpoint = f"{ATLASSIAN_ADMIN_BASE_URL}/admin/v1/orgs/{org_id}/users/search"
        expand = ["NAME", "EMAIL", "EMAIL_VERIFIED", "PRODUCT_LAST_ACCESS", "GROUPS"]

        all_users: list[dict] = []
        cursor: str | None = None

        while True:
            payload: dict[str, Any] = {"limit": 100, "expand": expand}
            if cursor:
                payload["cursor"] = cursor

            response = requests.post(endpoint, headers=headers, json=payload)
            response.raise_for_status()
            data = response.json()

            for raw in data.get("data", []):
                all_users.append({
                    "accountId": raw.get("accountId"),
                    "accountType": raw.get("accountType", "").lower(),
                    "accountStatus": raw.get("accountStatus", "").lower(),
                    "statusInUserbase": bool(raw.get("statusInUserbase")),
                    "name": raw.get("name", ""),
                    "email": raw.get("email", ""),
                    "groups": [
                        {"name": g.get("name"), "id": g.get("id")}
                        for g in raw.get("groups", [])
                    ],
                    "productLastAccess": raw.get("productLastAccess", []),
                })

            cursor = data.get("links", {}).get("next")
            if not cursor:
                break

        return all_users

    # ------------------------------------------------------------------
    # Inactivity analysis
    # ------------------------------------------------------------------

    @staticmethod
    def _user_last_access(user: dict) -> tuple[datetime | None, str | None, int | None]:
        """Return (last_access_dt, product_key, days_inactive) for a user."""
        last_access: datetime | None = None
        last_product: str | None = None

        for entry in user.get("productLastAccess") or []:
            ts = entry.get("lastActiveTimestamp")
            if not ts:
                continue
            try:
                dt = datetime.fromisoformat(ts.replace("Z", "+00:00"))
                if last_access is None or dt > last_access:
                    last_access = dt
                    last_product = entry.get("productKey")
            except (ValueError, TypeError):
                continue

        if last_access is None:
            return None, None, None

        return last_access, last_product, (datetime.now(UTC) - last_access).days

    def get_inactive_users(
        self,
        days: int = 30,
        account_type: str = "atlassian",
        include_no_activity: bool = False,
        exclude_account_ids: list[str] | None = None,
        exclude_group_ids: list[str] | None = None,
    ) -> list[dict[str, Any]]:
        """Find active org members who have not logged in for at least `days` days.

        Uses v2 to determine the correct set of active users (matching admin UI),
        then cross-references with v1 productLastAccess data to check inactivity.
        v2's `accountType` field is used to filter human users — no longer reliant
        on the v1 accountType for that filter.

        Args:
            days: Inactivity threshold in days.
            account_type: 'atlassian' for human accounts only.
            include_no_activity: Include users with no login history at all.
            exclude_account_ids: Account IDs to skip (whitelist).
            exclude_group_ids: Group IDs — members of these groups are skipped.

        Returns:
            List of user dicts enriched with _days_inactive, _last_access, _last_product.
        """
        # v2: accurate active set + accountType for human filter
        v2_active = self.fetch_org_users_v2(status_filter="active")
        active_ids: dict[str, dict] = {u["accountId"]: u for u in v2_active}
        logger.debug(f"v2 active user count: {len(active_ids)}")

        # v1: productLastAccess (and groups for exclude_group_ids)
        v1_users = self._fetch_org_users_v1_with_product_access()
        v1_by_id = {u["accountId"]: u for u in v1_users}

        result = []
        for account_id, v2_user in active_ids.items():
            if account_type and (v2_user.get("accountType") or "").lower() != account_type.lower():
                continue

            if exclude_account_ids and account_id in exclude_account_ids:
                continue

            if exclude_group_ids:
                v1_user = v1_by_id.get(account_id, {})
                user_group_ids = {g["id"] for g in v1_user.get("groups", []) if g.get("id")}
                if user_group_ids & set(exclude_group_ids):
                    continue

            v1_user = v1_by_id.get(account_id, {})
            last_access, last_product, days_inactive = self._user_last_access(v1_user)

            merged = {**v2_user, **{k: v for k, v in v1_user.items() if k not in v2_user}}

            if last_access is None:
                if include_no_activity:
                    result.append({
                        **merged,
                        "_days_inactive": None,
                        "_last_access": None,
                        "_last_product": None,
                    })
                continue

            if days_inactive >= days:
                result.append({
                    **merged,
                    "_days_inactive": days_inactive,
                    "_last_access": last_access.isoformat(),
                    "_last_product": last_product,
                })

        return result

    # ------------------------------------------------------------------
    # User lifecycle (v2)
    # ------------------------------------------------------------------

    def suspend_org_user(self, account_id: str, directory_id: str | None = None) -> bool:
        """Suspend an organization user by account ID (v2 API).

        Args:
            account_id: The Atlassian account ID to suspend.
            directory_id: Directory to act on. None uses wildcard '-'.
        """
        org_id, org_key = self._get_org_credentials()
        dir_id = self._dir_id(directory_id)
        url = (
            f"{ATLASSIAN_ADMIN_BASE_URL}/admin/v2/orgs/{org_id}"
            f"/directories/{dir_id}/users/{account_id}/suspend"
        )
        requests.post(url, headers=self._org_admin_headers(org_key)).raise_for_status()
        return True

    def reactivate_org_user(self, account_id: str, directory_id: str | None = None) -> bool:
        """Reactivate a suspended organization user by account ID (v2 API).

        Args:
            account_id: The Atlassian account ID to restore.
            directory_id: Directory to act on. None uses wildcard '-'.
        """
        org_id, org_key = self._get_org_credentials()
        dir_id = self._dir_id(directory_id)
        url = (
            f"{ATLASSIAN_ADMIN_BASE_URL}/admin/v2/orgs/{org_id}"
            f"/directories/{dir_id}/users/{account_id}/restore"
        )
        requests.post(url, headers=self._org_admin_headers(org_key)).raise_for_status()
        return True

    def remove_org_user_from_directory(
        self,
        account_id: str,
        directory_id: str | None = None,
    ) -> bool:
        """Remove a user from a directory (v2 API). Non-reversible.

        The user loses all roles and group memberships. You must re-invite them
        if you want to restore access. Billing stops immediately.

        Args:
            account_id: The Atlassian account ID to remove.
            directory_id: Directory to remove them from. None uses wildcard '-'.
        """
        org_id, org_key = self._get_org_credentials()
        dir_id = self._dir_id(directory_id)
        url = (
            f"{ATLASSIAN_ADMIN_BASE_URL}/admin/v2/orgs/{org_id}"
            f"/directories/{dir_id}/users/{account_id}"
        )
        requests.delete(url, headers=self._org_admin_headers(org_key)).raise_for_status()
        return True


_STATUS_LEGEND = {
    "active": "accountStatus=active AND membershipStatus=active (matches admin UI 'Active users')",
    "suspended": "accountStatus=active AND membershipStatus=suspended (access revoked)",
    "not_invited": "accountStatus=active but no directory membership",
    "deactivated": "accountStatus=inactive",
    "for_deletion": "managed account scheduled for deletion",
}
