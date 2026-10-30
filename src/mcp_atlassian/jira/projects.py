"""Module for Jira project operations."""

import json
import logging
import re
from typing import Any
from urllib.parse import urlencode

from requests.exceptions import HTTPError

from ..models import JiraProject
from ..models.jira.search import JiraSearchResult
from ..models.jira.version import JiraVersion
from .client import JiraClient
from .protocols import SearchOperationsProto
from .screens import _require_id

logger = logging.getLogger("mcp-jira")

_UUID_PATTERN = re.compile(
    r"^[0-9a-fA-F]{8}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{4}-[0-9a-fA-F]{12}$"
)


def _extract_actor_summary(actor: dict[str, Any]) -> dict[str, Any]:
    """Normalize a Jira role actor object into a structured summary dict."""
    actor_type = actor.get("type", "")
    display_name = actor.get("displayName") or actor.get("name") or ""
    actor_id = str(actor.get("id", ""))

    if "user" in actor_type.lower() or "actorUser" in actor:
        user_info = actor.get("actorUser") or {}
        account_id = user_info.get("accountId") or actor.get("name") or ""
        return {
            "type": "user",
            "id": actor_id,
            "account_id": account_id,
            "display_name": display_name,
        }
    if "group" in actor_type.lower() or "actorGroup" in actor:
        group_info = actor.get("actorGroup") or {}
        group_name = group_info.get("name") or actor.get("name") or display_name
        group_id = group_info.get("groupId")
        res: dict[str, Any] = {
            "type": "group",
            "id": actor_id,
            "name": group_name,
            "display_name": display_name,
        }
        if group_id:
            res["group_id"] = group_id
        return res
    return {
        "type": actor_type or "unknown",
        "id": actor_id,
        "display_name": display_name,
    }


class ProjectsMixin(JiraClient, SearchOperationsProto):
    """Mixin for Jira project operations.

    This mixin provides methods for retrieving and working with Jira projects,
    including project details, components, versions, and other project-related operations.
    """

    def get_all_projects(self, include_archived: bool = False) -> list[dict[str, Any]]:
        """
        Get all projects visible to the current user.

        Args:
            include_archived: Whether to include archived projects

        Returns:
            List of project data dictionaries
        """
        try:
            params = {}
            if include_archived:
                params["includeArchived"] = "true"

            projects = self.jira.projects(included_archived=include_archived)
            return projects if isinstance(projects, list) else []

        except Exception as e:
            logger.error(f"Error getting all projects: {str(e)}")
            return []

    def get_project(self, project_key: str) -> dict[str, Any] | None:
        """
        Get project information by key.

        Args:
            project_key: The project key (e.g. 'PROJ')

        Returns:
            Project data or None if not found
        """
        try:
            project_data = self.jira.project(project_key)
            if not isinstance(project_data, dict):
                msg = f"Unexpected return value type from `jira.project`: {type(project_data)}"
                logger.error(msg)
                raise TypeError(msg)
            return project_data
        except Exception as e:
            logger.warning(f"Error getting project {project_key}: {e}")
            return None

    def get_project_model(self, project_key: str) -> JiraProject | None:
        """
        Get project information as a JiraProject model.

        Args:
            project_key: The project key (e.g. 'PROJ')

        Returns:
            JiraProject model or None if not found
        """
        project_data = self.get_project(project_key)
        if not project_data:
            return None

        return JiraProject.from_api_response(project_data)

    def project_exists(self, project_key: str) -> bool:
        """
        Check if a project exists.

        Args:
            project_key: The project key to check

        Returns:
            True if the project exists, False otherwise
        """
        try:
            project = self.get_project(project_key)
            return project is not None

        except Exception:
            return False

    def get_project_components(self, project_key: str) -> list[dict[str, Any]]:
        """
        Get all components for a project.

        Args:
            project_key: The project key

        Returns:
            List of component data dictionaries
        """
        try:
            components = self.jira.get_project_components(key=project_key)
            return components if isinstance(components, list) else []

        except Exception as e:
            logger.error(
                f"Error getting components for project {project_key}: {str(e)}"
            )
            return []

    def get_project_versions(self, project_key: str) -> list[dict[str, Any]]:
        """
        Get all versions for a project.

        Args:
            project_key: The project key.

        Returns:
            List of version data dictionaries
        """
        try:
            raw_versions = self.jira.get_project_versions(key=project_key)
            if not isinstance(raw_versions, list):
                return []
            versions: list[dict[str, Any]] = []
            for v in raw_versions:
                ver = JiraVersion.from_api_response(v)
                versions.append(ver.to_simplified_dict())
            return versions
        except Exception as e:
            logger.error(f"Error getting versions for project {project_key}: {str(e)}")
            return []

    def get_project_roles(self, project_key: str) -> list[dict[str, Any]]:
        """Get all roles for a project with structured details.

        Queries the Jira v3 roledetails endpoint, falling back to the standard
        role endpoint if roledetails is unavailable.

        Args:
            project_key: The project key or ID.

        Returns:
            List of dictionaries with role id, name, description, actor_count,
            and actors summary.
        """
        project = _require_id("project_key", project_key)
        endpoint = f"rest/api/3/project/{project}/roledetails"
        response = None
        try:
            response = self.jira.get(endpoint)
        except Exception:
            response = None

        if isinstance(response, list):
            roles: list[dict[str, Any]] = []
            for r in response:
                if not isinstance(r, dict):
                    continue
                actors_raw = r.get("actors", [])
                actors = [
                    _extract_actor_summary(a) for a in actors_raw if isinstance(a, dict)
                ]
                roles.append(
                    {
                        "id": str(r.get("id", "")),
                        "name": r.get("name", ""),
                        "description": r.get("description", ""),
                        "actor_count": len(actors),
                        "actors": actors,
                    }
                )
            return roles

        # Fallback if roledetails is not supported or returns unexpected data
        try:
            raw_roles = self.jira.get_project_roles(project_key=project)
        except Exception as e:
            logger.error(f"Error getting roles for project {project}: {e}")
            return []

        if isinstance(raw_roles, dict):
            fallback_roles: list[dict[str, Any]] = []
            for name, val in raw_roles.items():
                role_id = ""
                description = ""
                if isinstance(val, dict):
                    role_id = str(val.get("id", ""))
                    description = val.get("description", "")
                elif isinstance(val, str):
                    match = re.search(r"/role/(\d+)/?$", val)
                    if match:
                        role_id = match.group(1)
                fallback_roles.append(
                    {
                        "id": role_id,
                        "name": name,
                        "description": description,
                        "actor_count": 0,
                        "actors": [],
                    }
                )
            return fallback_roles
        return []

    def resolve_project_role(
        self, project_key: str, role_id_or_name: str
    ) -> dict[str, Any]:
        """Resolve a role ID or role name to its role details in a project.

        Args:
            project_key: The project key or ID.
            role_id_or_name: Numeric role ID (e.g. '10002') or role name
                (e.g. 'Administrators').

        Returns:
            Dict representing the matched role (with 'id' and 'name').

        Raises:
            ValueError: If role_id_or_name is empty or role is not found.
        """
        identifier = str(role_id_or_name).strip() if role_id_or_name else ""
        if not identifier:
            msg = "role_id_or_name must not be empty"
            raise ValueError(msg)

        roles = self.get_project_roles(project_key)
        # First check by exact ID
        for role in roles:
            if role.get("id") and str(role["id"]) == identifier:
                return role

        # Then check by case-insensitive name
        for role in roles:
            if role.get("name") and role["name"].lower() == identifier.lower():
                return role

        # If not found, list available roles for self-correction
        available = [
            f"{r['name']} ({r['id']})" if r.get("id") else r["name"]
            for r in roles
            if r.get("name")
        ]
        roles_str = ", ".join(available) if available else "none"
        msg = (
            f"Role {identifier!r} not found in project {project_key!r}. "
            f"Available roles: {roles_str}"
        )
        raise ValueError(msg)

    def get_project_role_actors(
        self, project_key: str, role_id_or_name: str
    ) -> dict[str, Any]:
        """Get members assigned to a specific role in a project.

        Args:
            project_key: The project key or ID.
            role_id_or_name: Numeric role ID or role name.

        Returns:
            Dict containing project_key, role_id, role_name, description,
            users list, groups list, and total_actors.
        """
        project = _require_id("project_key", project_key)
        identifier = str(role_id_or_name).strip() if role_id_or_name else ""
        if not identifier:
            msg = "role_id_or_name must not be empty"
            raise ValueError(msg)

        role_name = ""
        description = ""
        if identifier.isdigit():
            role_id = identifier
        else:
            role = self.resolve_project_role(project, identifier)
            role_id = role["id"]
            role_name = role.get("name", "")
            description = role.get("description", "")

        endpoint = f"rest/api/3/project/{project}/role/{role_id}"
        role_data = None
        try:
            role_data = self.jira.get(endpoint)
        except Exception as e:
            logger.debug(
                f"Failed to fetch role {role_id} directly from {endpoint}: {e}"
            )

        if not isinstance(role_data, dict):
            try:
                raw_members = self.jira.get_project_actors_for_role_project(
                    project_key=project, role_id=role_id
                )
                role_data = raw_members if isinstance(raw_members, dict) else {}
            except Exception:
                role_data = {}

        if not role_name and isinstance(role_data, dict):
            role_name = role_data.get("name", "")
        if not description and isinstance(role_data, dict):
            description = role_data.get("description", "")

        actors_raw = role_data.get("actors", []) if isinstance(role_data, dict) else []
        users: list[dict[str, Any]] = []
        groups: list[dict[str, Any]] = []

        for actor in actors_raw:
            if not isinstance(actor, dict):
                continue
            summary = _extract_actor_summary(actor)
            if summary["type"] == "user":
                users.append(
                    {
                        "account_id": summary.get("account_id", ""),
                        "display_name": summary.get("display_name", ""),
                    }
                )
            elif summary["type"] == "group":
                g_dict: dict[str, Any] = {
                    "name": summary.get("name", ""),
                    "display_name": summary.get("display_name", ""),
                }
                if summary.get("group_id"):
                    g_dict["group_id"] = summary["group_id"]
                groups.append(g_dict)

        return {
            "project_key": project,
            "role_id": str(role_id),
            "role_name": role_name,
            "description": description,
            "users": users,
            "groups": groups,
            "total_actors": len(users) + len(groups),
        }

    def add_project_role_actors(
        self,
        project_key: str,
        role_id_or_name: str,
        users: str | None = None,
        groups: str | None = None,
        *,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Add actors (users and/or groups) to a project role.

        Args:
            project_key: The project key or ID.
            role_id_or_name: Numeric role ID or role name.
            users: Optional single account ID or comma-separated list of account IDs.
            groups: Optional single group name/ID or comma-separated list.
            dry_run: When True (default), report changes without calling API.

        Returns:
            Dry run preview or result of addition.
        """
        project = _require_id("project_key", project_key)
        role = self.resolve_project_role(project, role_id_or_name)
        role_id = role["id"]
        role_name = role.get("name", "")

        user_ids = [x.strip() for x in (users or "").split(",") if x.strip()]
        group_items = [x.strip() for x in (groups or "").split(",") if x.strip()]

        if not user_ids and not group_items:
            msg = (
                "At least one user (account ID) or group (name or ID) must be provided"
            )
            raise ValueError(msg)

        current = self.get_project_role_actors(project, role_id)
        existing_user_ids = {
            u["account_id"] for u in current.get("users", []) if u.get("account_id")
        }
        existing_group_ids = {
            g["group_id"] for g in current.get("groups", []) if g.get("group_id")
        }
        existing_group_names = {
            g["name"] for g in current.get("groups", []) if g.get("name")
        }

        # Deduplicate users
        users_to_add: list[str] = []
        already_users: list[str] = []
        for uid in user_ids:
            if uid in existing_user_ids:
                if uid not in already_users:
                    already_users.append(uid)
            else:
                if uid not in users_to_add:
                    users_to_add.append(uid)

        # Deduplicate groups and categorize into groupId vs group name
        group_ids_to_add: list[str] = []
        group_names_to_add: list[str] = []
        already_groups: list[str] = []

        for g in group_items:
            is_uuid = bool(_UUID_PATTERN.match(g))
            if is_uuid:
                if g in existing_group_ids:
                    if g not in already_groups:
                        already_groups.append(g)
                else:
                    if g not in group_ids_to_add:
                        group_ids_to_add.append(g)
            else:
                if g in existing_group_names:
                    if g not in already_groups:
                        already_groups.append(g)
                else:
                    if g not in group_names_to_add:
                        group_names_to_add.append(g)

        endpoint = f"rest/api/3/project/{project}/role/{role_id}"
        payload: dict[str, list[str]] = {}
        if users_to_add:
            payload["user"] = users_to_add
        if group_ids_to_add:
            payload["groupId"] = group_ids_to_add
        if group_names_to_add:
            payload["group"] = group_names_to_add

        would_add = {
            "users": users_to_add,
            "groups": group_ids_to_add + group_names_to_add,
        }
        already_members = {
            "users": already_users,
            "groups": already_groups,
        }

        if dry_run:
            return {
                "success": True,
                "dry_run": True,
                "action": "add_project_role_actors",
                "project_key": project,
                "role_id": str(role_id),
                "role_name": role_name,
                "would_add": would_add,
                "already_members": already_members,
                "endpoint": endpoint,
                "payload": payload,
            }

        if payload:
            self.jira.post(endpoint, data=payload)

        return {
            "success": True,
            "dry_run": False,
            "action": "add_project_role_actors",
            "project_key": project,
            "role_id": str(role_id),
            "role_name": role_name,
            "added": would_add,
            "already_members": already_members,
        }

    def remove_project_role_actors(
        self,
        project_key: str,
        role_id_or_name: str,
        users: str | None = None,
        groups: str | None = None,
        *,
        dry_run: bool = True,
    ) -> dict[str, Any]:
        """Remove actors (users and/or groups) from a project role.

        Args:
            project_key: The project key or ID.
            role_id_or_name: Numeric role ID or role name.
            users: Optional single account ID or comma-separated list of account IDs.
            groups: Optional single group name/ID or comma-separated list.
            dry_run: When True (default), report changes without calling API.

        Returns:
            Dry run preview or result of removal.
        """
        project = _require_id("project_key", project_key)
        role = self.resolve_project_role(project, role_id_or_name)
        role_id = role["id"]
        role_name = role.get("name", "")

        user_ids = [x.strip() for x in (users or "").split(",") if x.strip()]
        group_items = [x.strip() for x in (groups or "").split(",") if x.strip()]

        if not user_ids and not group_items:
            msg = (
                "At least one user (account ID) or group (name or ID) must be provided"
            )
            raise ValueError(msg)

        current = self.get_project_role_actors(project, role_id)
        existing_user_ids = {
            u["account_id"] for u in current.get("users", []) if u.get("account_id")
        }
        existing_group_ids = {
            g["group_id"] for g in current.get("groups", []) if g.get("group_id")
        }
        existing_group_names = {
            g["name"] for g in current.get("groups", []) if g.get("name")
        }

        users_to_remove: list[str] = []
        users_not_in_role: list[str] = []
        for uid in user_ids:
            if uid in existing_user_ids:
                if uid not in users_to_remove:
                    users_to_remove.append(uid)
            else:
                if uid not in users_not_in_role:
                    users_not_in_role.append(uid)

        groups_to_remove: list[dict[str, str]] = []
        groups_not_in_role: list[str] = []

        for g in group_items:
            is_uuid = bool(_UUID_PATTERN.match(g))
            if is_uuid and g in existing_group_ids:
                groups_to_remove.append({"param": "groupId", "value": g})
            elif not is_uuid and g in existing_group_names:
                groups_to_remove.append({"param": "group", "value": g})
            else:
                groups_not_in_role.append(g)

        endpoint = f"rest/api/3/project/{project}/role/{role_id}"
        would_remove = {
            "users": users_to_remove,
            "groups": [item["value"] for item in groups_to_remove],
        }
        not_in_role = {
            "users": users_not_in_role,
            "groups": groups_not_in_role,
        }

        if dry_run:
            return {
                "success": True,
                "dry_run": True,
                "action": "remove_project_role_actors",
                "project_key": project,
                "role_id": str(role_id),
                "role_name": role_name,
                "would_remove": would_remove,
                "not_in_role": not_in_role,
                "endpoint": endpoint,
            }

        # Execute DELETE requests sequentially
        for uid in users_to_remove:
            self.jira.delete(f"{endpoint}?{urlencode({'user': uid})}")

        for g_item in groups_to_remove:
            self.jira.delete(
                f"{endpoint}?{urlencode({g_item['param']: g_item['value']})}"
            )

        return {
            "success": True,
            "dry_run": False,
            "action": "remove_project_role_actors",
            "project_key": project,
            "role_id": str(role_id),
            "role_name": role_name,
            "removed": would_remove,
            "not_in_role": not_in_role,
        }

    def get_project_role_members(
        self, project_key: str, role_id: str
    ) -> list[dict[str, Any]]:
        """
        Get members assigned to a specific role in a project.

        Args:
            project_key: The project key
            role_id: The role ID

        Returns:
            List of role members
        """
        try:
            members = self.jira.get_project_actors_for_role_project(
                project_key=project_key, role_id=role_id
            )
            # Extract the actors from the response
            actors = []
            if isinstance(members, dict) and "actors" in members:
                actors = members.get("actors", [])
            return actors

        except Exception as e:
            logger.error(
                f"Error getting role members for project {project_key}, role {role_id}: {str(e)}"
            )
            return []

    def get_project_permission_scheme(self, project_key: str) -> dict[str, Any] | None:
        """
        Get the permission scheme for a project.

        Args:
            project_key: The project key

        Returns:
            Permission scheme data if found, None otherwise
        """
        try:
            scheme = self.jira.get_project_permission_scheme(
                project_id_or_key=project_key
            )
            if not isinstance(scheme, dict):
                msg = f"Unexpected return value type from `jira.get_project_permission_scheme`: {type(scheme)}"
                logger.error(msg)
                raise TypeError(msg)
            return scheme

        except Exception as e:
            logger.error(
                f"Error getting permission scheme for project {project_key}: {str(e)}"
            )
            return None

    def get_project_notification_scheme(
        self, project_key: str
    ) -> dict[str, Any] | None:
        """
        Get the notification scheme for a project.

        Args:
            project_key: The project key

        Returns:
            Notification scheme data if found, None otherwise
        """
        try:
            scheme = self.jira.get_project_notification_scheme(
                project_id_or_key=project_key
            )
            if not isinstance(scheme, dict):
                msg = f"Unexpected return value type from `jira.get_project_notification_scheme`: {type(scheme)}"
                logger.error(msg)
                raise TypeError(msg)
            return scheme

        except Exception as e:
            logger.error(
                f"Error getting notification scheme for project {project_key}: {str(e)}"
            )
            return None

    def get_project_issue_types(self, project_key: str) -> list[dict[str, Any]]:
        """
        Get all issue types available for a project.

        Args:
            project_key: The project key

        Returns:
            List of issue type data dictionaries
        """
        try:
            project = self.jira.get(f"rest/api/3/project/{project_key}")
            if not isinstance(project, dict) or not project.get("id"):
                raise TypeError(
                    f"Unexpected project response while resolving {project_key}"
                )
            response = self.jira.get(
                "rest/api/3/issuetype/project",
                params={"projectId": str(project["id"])},
            )
            if not isinstance(response, list):
                msg = f"Unexpected response type from project issue types: {type(response)}"
                logger.error(msg)
                raise TypeError(msg)
            return response

        except Exception as e:
            logger.error(
                f"Error getting issue types for project {project_key}: {str(e)}"
            )
            return []

    def get_project_statuses(self, project_key: str) -> list[dict[str, Any]]:
        """Get statuses available to a project, grouped by issue type."""
        response = self.jira.get(f"rest/api/3/project/{project_key}/statuses")
        if not isinstance(response, list):
            raise TypeError(
                f"Unexpected response type from project statuses: {type(response)}"
            )
        return response

    def get_workflow_scheme_for_project(self, project_key: str) -> dict[str, Any]:
        """Get the workflow scheme associated with a project."""
        project = self.jira.get(f"rest/api/3/project/{project_key}")
        if not isinstance(project, dict) or not project.get("id"):
            raise TypeError(
                f"Unexpected project response while resolving {project_key}"
            )
        response = self.jira.get(
            "rest/api/3/workflowscheme/project",
            params={"projectId": str(project["id"])},
        )
        if not isinstance(response, dict):
            raise TypeError(
                f"Unexpected response type from project workflow scheme: {type(response)}"
            )
        return response

    def get_project_issues_count(self, project_key: str) -> int:
        """
        Get the total number of issues in a project.

        Args:
            project_key: The project key

        Returns:
            Count of issues in the project
        """
        try:
            # Use JQL to count issues in the project
            jql = f'project = "{project_key}"'
            result = self.jira.jql(jql=jql, fields="key", limit=1)
            if not isinstance(result, dict):
                msg = f"Unexpected return value type from `jira.jql`: {type(result)}"
                logger.error(msg)
                raise TypeError(msg)

            # Extract total from the response
            total = 0
            if isinstance(result, dict) and "total" in result:
                total = result.get("total", 0)

            return total

        except Exception as e:
            logger.error(
                f"Error getting issue count for project {project_key}: {str(e)}"
            )
            return 0

    def get_project_issues(
        self, project_key: str, start: int = 0, limit: int = 50
    ) -> JiraSearchResult:
        """
        Get issues for a specific project.

        Args:
            project_key: The project key
            start: Index of the first issue to return
            limit: Maximum number of issues to return

        Returns:
            List of JiraIssue models representing the issues
        """
        try:
            # Use JQL to get issues in the project
            jql = f'project = "{project_key}"'

            return self.search_issues(jql, start=start, limit=limit)

        except Exception as e:
            logger.error(f"Error getting issues for project {project_key}: {str(e)}")
            return JiraSearchResult(issues=[], total=0)

    def get_project_keys(self) -> list[str]:
        """
        Get all project keys.

        Returns:
            List of project keys
        """
        try:
            projects = self.get_all_projects()
            project_keys: list[str] = []
            for project in projects:
                key = project.get("key")
                if not isinstance(key, str):
                    msg = f"Unexpected return value type from `get_all_projects`: {type(key)}"
                    logger.error(msg)
                    raise TypeError(msg)
                project_keys.append(key)
            return project_keys

        except Exception as e:
            logger.error(f"Error getting project keys: {str(e)}")
            return []

    def get_project_leads(self) -> dict[str, str]:
        """
        Get all project leads mapped to their projects.

        Returns:
            Dictionary mapping project keys to lead usernames
        """
        try:
            projects = self.get_all_projects()
            leads = {}

            for project in projects:
                if "key" in project and "lead" in project:
                    key = project.get("key")
                    lead = project.get("lead", {})

                    # Handle different formats of lead information
                    lead_name = None
                    if isinstance(lead, dict):
                        lead_name = lead.get("name") or lead.get("displayName")
                    elif isinstance(lead, str):
                        lead_name = lead

                    if key and lead_name:
                        leads[key] = lead_name

            return leads

        except Exception as e:
            logger.error(f"Error getting project leads: {str(e)}")
            return {}

    def get_user_accessible_projects(self, username: str) -> list[dict[str, Any]]:
        """
        Get projects that a specific user can access.

        Args:
            username: The username to check access for

        Returns:
            List of accessible project data dictionaries
        """
        try:
            # This requires admin permissions
            # For non-admins, a different approach might be needed
            all_projects = self.get_all_projects()
            accessible_projects = []

            for project in all_projects:
                project_key = project.get("key")
                if not project_key:
                    continue

                try:
                    # Check if user has browse permission for this project
                    browse_users = (
                        self.jira.get_users_with_browse_permission_to_a_project(
                            username=username, project_key=project_key, limit=1
                        )
                    )

                    # If the user is in the list, they have access
                    user_has_access = False
                    if isinstance(browse_users, list):
                        for user in browse_users:
                            if isinstance(user, dict) and user.get("name") == username:
                                user_has_access = True
                                break

                    if user_has_access:
                        accessible_projects.append(project)

                except Exception:
                    # Skip projects that cause errors
                    continue

            return accessible_projects

        except Exception as e:
            logger.error(
                f"Error getting accessible projects for user {username}: {str(e)}"
            )
            return []

    def create_project_version(
        self,
        project_key: str,
        name: str,
        start_date: str = None,
        release_date: str = None,
        description: str = None,
    ) -> dict[str, Any]:
        """
        Create a new version in the specified Jira project.

        Args:
            project_key: The project key (e.g., 'PROJ')
            name: The name of the version
            start_date: The start date (YYYY-MM-DD, optional)
            release_date: The release date (YYYY-MM-DD, optional)
            description: Description of the version (optional)

        Returns:
            The created version object as returned by Jira
        """
        return self.create_version(
            project=project_key,
            name=name,
            start_date=start_date,
            release_date=release_date,
            description=description,
        )

    def get_project_properties(
        self,
        project_key_or_id: str,
        property_key: str | None = None,
        max_length: int = 15000,
    ) -> dict[str, Any]:
        """Get all property keys or a specific property value for a Jira project.

        Args:
            project_key_or_id: The project key (e.g. 'SST') or numeric ID.
            property_key: Optional property key. If omitted, returns all available
                property keys for this project. If provided, returns the property value.
            max_length: Maximum character length for serialized JSON property values.
                Larger responses are truncated with `truncated: True`.

        Returns:
            Dictionary with project properties data.
        """
        if not project_key_or_id:
            raise ValueError("Project key or ID is required")

        if property_key:
            endpoint = (
                f"rest/api/3/project/{project_key_or_id}/properties/{property_key}"
            )
            try:
                response = self.jira.get(endpoint)
            except HTTPError as e:
                if e.response is not None and e.response.status_code == 404:
                    raise ValueError(
                        f"Ikke funnet: egenskapen '{property_key}' eller prosjektet '{project_key_or_id}' finnes ikke."
                    ) from e
                if e.response is not None and e.response.status_code == 403:
                    raise PermissionError(
                        f"Ikke tillatt for denne brukeren: mangler tilgang til prosjekt '{project_key_or_id}' eller egenskapen."
                    ) from e
                raise

            if not isinstance(response, dict):
                raise TypeError(
                    f"Unexpected response type from Jira API: {type(response)}"
                )

            value = response.get("value")
            truncated = False
            val_json = json.dumps(value, ensure_ascii=False)
            if len(val_json) > max_length:
                truncated = True
                value = {"_truncated_preview": val_json[:max_length] + "..."}

            return {
                "project": project_key_or_id,
                "key": property_key,
                "value": value,
                "truncated": truncated,
            }

        endpoint = f"rest/api/3/project/{project_key_or_id}/properties"
        try:
            response = self.jira.get(endpoint)
        except HTTPError as e:
            if e.response is not None and e.response.status_code == 404:
                raise ValueError(
                    f"Ikke funnet: prosjektet '{project_key_or_id}' finnes ikke."
                ) from e
            if e.response is not None and e.response.status_code == 403:
                raise PermissionError(
                    f"Ikke tillatt for denne brukeren: mangler lesetilgang til prosjekt '{project_key_or_id}'."
                ) from e
            raise

        if not isinstance(response, dict):
            raise TypeError(f"Unexpected response type from Jira API: {type(response)}")

        keys = [item["key"] for item in response.get("keys", []) if "key" in item]
        return {
            "project": project_key_or_id,
            "keys": keys,
            "total_keys": len(keys),
        }
