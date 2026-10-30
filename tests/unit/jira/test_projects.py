"""Tests for the Jira ProjectsMixin."""

from typing import Any
from unittest.mock import MagicMock, call, patch

import pytest

from mcp_atlassian.jira import JiraFetcher
from mcp_atlassian.jira.config import JiraConfig
from mcp_atlassian.jira.projects import ProjectsMixin
from mcp_atlassian.models.jira.issue import JiraIssue
from mcp_atlassian.models.jira.search import JiraSearchResult


@pytest.fixture
def mock_config():
    """Fixture to create a mock JiraConfig instance."""
    config = MagicMock(spec=JiraConfig)
    config.url = "https://test.atlassian.net"
    config.username = "test@example.com"
    config.api_token = "test-token"
    config.auth_type = "pat"
    return config


@pytest.fixture
def projects_mixin(jira_fetcher: JiraFetcher) -> ProjectsMixin:
    """Fixture to create a ProjectsMixin instance for testing."""
    mixin = jira_fetcher
    return mixin


@pytest.fixture
def mock_projects():
    """Fixture to return mock project data."""
    return [
        {
            "id": "10000",
            "key": "PROJ1",
            "name": "Project One",
            "lead": {"name": "user1", "displayName": "User One"},
        },
        {
            "id": "10001",
            "key": "PROJ2",
            "name": "Project Two",
            "lead": {"name": "user2", "displayName": "User Two"},
        },
    ]


@pytest.fixture
def mock_components():
    """Fixture to return mock project components."""
    return [
        {"id": "10000", "name": "Component One"},
        {"id": "10001", "name": "Component Two"},
    ]


@pytest.fixture
def mock_versions():
    """Fixture to return mock project versions."""
    return [
        {"id": "10000", "name": "1.0", "released": True},
        {"id": "10001", "name": "2.0", "released": False},
    ]


@pytest.fixture
def mock_roles():
    """Fixture to return mock project roles."""
    return {
        "Administrator": {"id": "10000", "name": "Administrator"},
        "Developer": {"id": "10001", "name": "Developer"},
    }


@pytest.fixture
def mock_role_members():
    """Fixture to return mock project role members."""
    return {
        "actors": [
            {"id": "user1", "name": "user1", "displayName": "User One"},
            {"id": "user2", "name": "user2", "displayName": "User Two"},
        ]
    }


@pytest.fixture
def mock_issue_types():
    """Fixture to return mock issue types."""
    return [
        {"id": "10000", "name": "Bug", "description": "A bug"},
        {"id": "10001", "name": "Task", "description": "A task"},
    ]


def test_get_all_projects(projects_mixin: ProjectsMixin, mock_projects: list[dict]):
    """Test get_all_projects method."""
    projects_mixin.jira.projects.return_value = mock_projects

    # Test with default value (include_archived=False)
    result = projects_mixin.get_all_projects()
    assert result == mock_projects
    projects_mixin.jira.projects.assert_called_once_with(included_archived=False)

    # Reset mock and test with include_archived=True
    projects_mixin.jira.projects.reset_mock()
    projects_mixin.jira.projects.return_value = mock_projects

    result = projects_mixin.get_all_projects(include_archived=True)
    assert result == mock_projects
    projects_mixin.jira.projects.assert_called_once_with(included_archived=True)


def test_get_all_projects_exception(projects_mixin: ProjectsMixin):
    """Test get_all_projects method with exception."""
    projects_mixin.jira.projects.side_effect = Exception("API error")

    result = projects_mixin.get_all_projects()
    assert result == []
    projects_mixin.jira.projects.assert_called_once()


def test_get_all_projects_non_list_response(projects_mixin: ProjectsMixin):
    """Test get_all_projects method with non-list response."""
    projects_mixin.jira.projects.return_value = "not a list"

    result = projects_mixin.get_all_projects()
    assert result == []
    projects_mixin.jira.projects.assert_called_once()


def test_get_project(projects_mixin: ProjectsMixin, mock_projects: list[dict]):
    """Test get_project method."""
    project = mock_projects[0]
    projects_mixin.jira.project.return_value = project

    result = projects_mixin.get_project("PROJ1")
    assert result == project
    projects_mixin.jira.project.assert_called_once_with("PROJ1")


def test_get_project_exception(projects_mixin: ProjectsMixin):
    """Test get_project method with exception."""
    projects_mixin.jira.project.side_effect = Exception("API error")

    result = projects_mixin.get_project("PROJ1")
    assert result is None
    projects_mixin.jira.project.assert_called_once()


def test_get_project_issues(projects_mixin: ProjectsMixin):
    """Test get_project_issues method."""
    # Setup mock response
    # Mock the method that is actually called: search_issues
    # It should return a JiraSearchResult object
    mock_search_result = JiraSearchResult(
        issues=[], total=0, start_at=0, max_results=50
    )
    projects_mixin.search_issues = MagicMock(return_value=mock_search_result)

    # Call the method
    result = projects_mixin.get_project_issues("TEST")

    # Verify search_issues was called, not jira.jql
    projects_mixin.search_issues.assert_called_once_with(
        'project = "TEST"',
        start=0,
        limit=50,
    )
    assert isinstance(result, JiraSearchResult)
    assert len(result.issues) == 0


def test_get_project_issues_with_start(projects_mixin: ProjectsMixin) -> None:
    """Test getting project issues with a start index."""
    # Mock search_issues to return a result reflecting pagination
    mock_issue = JiraIssue(key="PROJ-2", summary="Issue 2", id="10002")
    mock_search_result = JiraSearchResult(
        issues=[mock_issue],
        total=1,
        start_at=3,
        max_results=5,
    )
    projects_mixin.search_issues = MagicMock(return_value=mock_search_result)

    project_key = "PROJ"
    start_index = 3

    # Call the method
    result = projects_mixin.get_project_issues(project_key, start=start_index, limit=5)

    assert len(result.issues) == 1
    # Note: Assertions on start_at, max_results, total should be based on the
    # JiraSearchResult object returned by the mocked search_issues
    assert result.start_at == 3  # Comes from the mocked JiraSearchResult
    assert result.max_results == 5  # Comes from the mocked JiraSearchResult
    assert result.total == 1  # Comes from the mocked JiraSearchResult

    # Verify search_issues was called with the correct arguments
    projects_mixin.search_issues.assert_called_once_with(
        f'project = "{project_key}"',
        start=start_index,
        limit=5,
    )


def test_project_exists(projects_mixin: ProjectsMixin, mock_projects: list[dict]):
    """Test project_exists method."""
    # Test with existing project
    project = mock_projects[0]
    projects_mixin.jira.project.return_value = project

    result = projects_mixin.project_exists("PROJ1")
    assert result is True
    projects_mixin.jira.project.assert_called_once_with("PROJ1")

    # Test with non-existing project
    projects_mixin.jira.project.reset_mock()
    projects_mixin.jira.project.return_value = None

    result = projects_mixin.project_exists("NONEXISTENT")
    assert result is False
    projects_mixin.jira.project.assert_called_once()


def test_project_exists_exception(projects_mixin: ProjectsMixin):
    """Test project_exists method with exception."""
    projects_mixin.jira.project.side_effect = Exception("API error")

    result = projects_mixin.project_exists("PROJ1")
    assert result is False
    projects_mixin.jira.project.assert_called_once()


def test_get_project_components(
    projects_mixin: ProjectsMixin, mock_components: list[dict]
):
    """Test get_project_components method."""
    projects_mixin.jira.get_project_components.return_value = mock_components

    result = projects_mixin.get_project_components("PROJ1")
    assert result == mock_components
    projects_mixin.jira.get_project_components.assert_called_once_with(key="PROJ1")


def test_get_project_components_exception(projects_mixin: ProjectsMixin):
    """Test get_project_components method with exception."""
    projects_mixin.jira.get_project_components.side_effect = Exception("API error")

    result = projects_mixin.get_project_components("PROJ1")
    assert result == []
    projects_mixin.jira.get_project_components.assert_called_once()


def test_get_project_components_non_list_response(projects_mixin: ProjectsMixin):
    """Test get_project_components method with non-list response."""
    projects_mixin.jira.get_project_components.return_value = "not a list"

    result = projects_mixin.get_project_components("PROJ1")
    assert result == []
    projects_mixin.jira.get_project_components.assert_called_once()


def test_get_project_versions(projects_mixin: ProjectsMixin, mock_versions: list[dict]):
    """Test get_project_versions method."""
    projects_mixin.jira.get_project_versions.return_value = mock_versions
    # Simplified dicts should include id, name, released and archived
    expected = [
        {
            "id": v["id"],
            "name": v["name"],
            "released": v.get("released", False),
            "archived": v.get("archived", False),
        }
        for v in mock_versions
    ]
    result = projects_mixin.get_project_versions("PROJ1")
    assert result == expected
    projects_mixin.jira.get_project_versions.assert_called_once_with(key="PROJ1")


def test_get_project_versions_exception(projects_mixin: ProjectsMixin):
    """Test get_project_versions method with exception."""
    projects_mixin.jira.get_project_versions.side_effect = Exception("API error")
    result = projects_mixin.get_project_versions("PROJ1")
    assert result == []
    projects_mixin.jira.get_project_versions.assert_called_once_with(key="PROJ1")


def test_get_project_versions_non_list_response(projects_mixin: ProjectsMixin):
    """Test get_project_versions method with non-list response."""
    projects_mixin.jira.get_project_versions.return_value = "not a list"
    result = projects_mixin.get_project_versions("PROJ1")
    assert result == []
    projects_mixin.jira.get_project_versions.assert_called_once_with(key="PROJ1")


def test_get_project_roles_fallback(
    projects_mixin: ProjectsMixin, mock_roles: dict[str, dict[str, str]]
):
    """Test get_project_roles method with fallback to raw roles dict."""
    projects_mixin.jira.get.side_effect = Exception("404 Not Found")
    projects_mixin.jira.get_project_roles.return_value = mock_roles

    result = projects_mixin.get_project_roles("PROJ1")
    assert len(result) == 2
    assert result[0]["name"] == "Administrator"
    assert result[0]["id"] == "10000"
    assert result[1]["name"] == "Developer"
    assert result[1]["id"] == "10001"
    projects_mixin.jira.get_project_roles.assert_called_once_with(project_key="PROJ1")


def test_get_project_roles_roledetails(projects_mixin: ProjectsMixin):
    """Test get_project_roles using Jira v3 roledetails endpoint."""
    projects_mixin.jira.get.return_value = [
        {
            "id": 10002,
            "name": "Administrators",
            "description": "Project admin role",
            "actors": [
                {
                    "id": 100,
                    "type": "atlassian-user-role-actor",
                    "displayName": "Alice Admin",
                    "actorUser": {"accountId": "account-123"},
                },
                {
                    "id": 101,
                    "type": "atlassian-group-role-actor",
                    "displayName": "jira-admins",
                    "actorGroup": {
                        "name": "jira-admins",
                        "groupId": "11111111-2222-3333-4444-555555555555",
                    },
                },
            ],
        }
    ]

    result = projects_mixin.get_project_roles("PROJ1")
    assert len(result) == 1
    role = result[0]
    assert role["id"] == "10002"
    assert role["name"] == "Administrators"
    assert role["description"] == "Project admin role"
    assert role["actor_count"] == 2
    assert len(role["actors"]) == 2
    assert role["actors"][0]["type"] == "user"
    assert role["actors"][0]["account_id"] == "account-123"
    assert role["actors"][1]["type"] == "group"
    assert role["actors"][1]["group_id"] == "11111111-2222-3333-4444-555555555555"


def test_get_project_roles_exception(projects_mixin: ProjectsMixin):
    """Test get_project_roles method with exception."""
    projects_mixin.jira.get.side_effect = Exception("API error")
    projects_mixin.jira.get_project_roles.side_effect = Exception("API error")

    result = projects_mixin.get_project_roles("PROJ1")
    assert result == []
    projects_mixin.jira.get_project_roles.assert_called_once()


def test_get_project_roles_non_dict_response(projects_mixin: ProjectsMixin):
    """Test get_project_roles method with non-dict fallback response."""
    projects_mixin.jira.get.side_effect = Exception("API error")
    projects_mixin.jira.get_project_roles.return_value = "not a dict"

    result = projects_mixin.get_project_roles("PROJ1")
    assert result == []
    projects_mixin.jira.get_project_roles.assert_called_once()


def test_resolve_project_role(projects_mixin: ProjectsMixin):
    """Test resolve_project_role by ID, name, and not found."""
    projects_mixin.jira.get.return_value = [
        {"id": "10002", "name": "Administrators", "actors": []},
        {"id": "10003", "name": "Developers", "actors": []},
    ]

    # Resolve by ID
    by_id = projects_mixin.resolve_project_role("PROJ1", "10002")
    assert by_id["name"] == "Administrators"

    # Resolve by name (case-insensitive)
    by_name = projects_mixin.resolve_project_role("PROJ1", "administrators")
    assert by_name["id"] == "10002"

    # Not found raises ValueError with available roles
    with pytest.raises(ValueError, match="Administrators \\(10002\\)"):
        projects_mixin.resolve_project_role("PROJ1", "UnknownRole")

    # Empty raises ValueError
    with pytest.raises(ValueError, match="must not be empty"):
        projects_mixin.resolve_project_role("PROJ1", "")


def test_get_project_role_actors(projects_mixin: ProjectsMixin):
    """Test get_project_role_actors formatting of users and groups."""
    projects_mixin.jira.get.side_effect = [
        # First call: get_project_roles for resolution
        [{"id": "10002", "name": "Administrators", "actors": []}],
        # Second call: get specific role
        {
            "id": 10002,
            "name": "Administrators",
            "description": "Admin role",
            "actors": [
                {
                    "id": 1,
                    "type": "atlassian-user-role-actor",
                    "displayName": "User One",
                    "actorUser": {"accountId": "acc-1"},
                },
                {
                    "id": 2,
                    "type": "atlassian-group-role-actor",
                    "displayName": "grp-team",
                    "actorGroup": {
                        "name": "grp-team",
                        "groupId": "11111111-2222-3333-4444-555555555555",
                    },
                },
            ],
        },
    ]

    result = projects_mixin.get_project_role_actors("PROJ1", "Administrators")
    assert result["project_key"] == "PROJ1"
    assert result["role_id"] == "10002"
    assert result["role_name"] == "Administrators"
    assert result["total_actors"] == 2
    assert len(result["users"]) == 1
    assert result["users"][0]["account_id"] == "acc-1"
    assert len(result["groups"]) == 1
    assert result["groups"][0]["group_id"] == "11111111-2222-3333-4444-555555555555"


def test_add_project_role_actors_dry_run_and_apply(projects_mixin: ProjectsMixin):
    """Test adding role actors with dry_run and applying changes."""
    roles_mock = [{"id": "10002", "name": "Administrators", "actors": []}]
    role_mock = {
        "id": 10002,
        "name": "Administrators",
        "description": "Admin role",
        "actors": [
            {
                "id": 1,
                "type": "atlassian-user-role-actor",
                "displayName": "Existing User",
                "actorUser": {"accountId": "existing-acc"},
            }
        ],
    }

    # 1. Dry run with duplicate and new actors
    projects_mixin.jira.get.side_effect = [roles_mock, role_mock]
    preview = projects_mixin.add_project_role_actors(
        "PROJ1",
        "10002",
        users="existing-acc, new-acc",
        groups="jira-software-users",
        dry_run=True,
    )
    assert preview["dry_run"] is True
    assert preview["would_add"]["users"] == ["new-acc"]
    assert preview["would_add"]["groups"] == ["jira-software-users"]
    assert preview["already_members"]["users"] == ["existing-acc"]
    assert projects_mixin.jira.post.call_count == 0

    # 2. Apply (dry_run=False)
    projects_mixin.jira.get.side_effect = [roles_mock, role_mock]
    result = projects_mixin.add_project_role_actors(
        "PROJ1",
        "10002",
        users="new-acc",
        groups="11111111-2222-3333-4444-555555555555",
        dry_run=False,
    )
    assert result["dry_run"] is False
    assert result["added"]["users"] == ["new-acc"]
    assert result["added"]["groups"] == ["11111111-2222-3333-4444-555555555555"]
    projects_mixin.jira.post.assert_called_once_with(
        "rest/api/3/project/PROJ1/role/10002",
        data={
            "user": ["new-acc"],
            "groupId": ["11111111-2222-3333-4444-555555555555"],
        },
    )


def test_add_project_role_actors_validation(projects_mixin: ProjectsMixin):
    """Test validation when no users or groups are provided."""
    projects_mixin.jira.get.return_value = [
        {"id": "10002", "name": "Administrators", "actors": []}
    ]
    with pytest.raises(ValueError, match="At least one user"):
        projects_mixin.add_project_role_actors("PROJ1", "10002")


def test_remove_project_role_actors_dry_run_and_apply(projects_mixin: ProjectsMixin):
    """Test removing role actors with dry_run and applying deletion."""
    roles_mock = [{"id": "10002", "name": "Administrators", "actors": []}]
    role_mock = {
        "id": 10002,
        "name": "Administrators",
        "description": "Admin role",
        "actors": [
            {
                "id": 1,
                "type": "atlassian-user-role-actor",
                "displayName": "User One",
                "actorUser": {"accountId": "acc-to-remove"},
            },
            {
                "id": 2,
                "type": "atlassian-group-role-actor",
                "displayName": "group-to-remove",
                "actorGroup": {
                    "name": "group-to-remove",
                    "groupId": "22222222-3333-4444-5555-666666666666",
                },
            },
        ],
    }

    # 1. Dry run
    projects_mixin.jira.get.side_effect = [roles_mock, role_mock]
    preview = projects_mixin.remove_project_role_actors(
        "PROJ1",
        "Administrators",
        users="acc-to-remove, acc-not-there",
        groups="group-to-remove, group-not-there",
        dry_run=True,
    )
    assert preview["dry_run"] is True
    assert preview["would_remove"]["users"] == ["acc-to-remove"]
    assert preview["would_remove"]["groups"] == ["group-to-remove"]
    assert preview["not_in_role"]["users"] == ["acc-not-there"]
    assert preview["not_in_role"]["groups"] == ["group-not-there"]
    assert projects_mixin.jira.delete.call_count == 0

    # 2. Apply (dry_run=False)
    projects_mixin.jira.get.side_effect = [roles_mock, role_mock]
    result = projects_mixin.remove_project_role_actors(
        "PROJ1",
        "10002",
        users="acc-to-remove",
        groups="22222222-3333-4444-5555-666666666666",
        dry_run=False,
    )
    assert result["dry_run"] is False
    assert result["removed"]["users"] == ["acc-to-remove"]
    assert result["removed"]["groups"] == ["22222222-3333-4444-5555-666666666666"]
    assert projects_mixin.jira.delete.call_count == 2
    projects_mixin.jira.delete.assert_any_call(
        "rest/api/3/project/PROJ1/role/10002?user=acc-to-remove"
    )
    projects_mixin.jira.delete.assert_any_call(
        "rest/api/3/project/PROJ1/role/10002?groupId=22222222-3333-4444-5555-666666666666"
    )


def test_remove_project_role_actors_validation(projects_mixin: ProjectsMixin):
    """Test validation when no users or groups are provided for removal."""
    projects_mixin.jira.get.return_value = [
        {"id": "10002", "name": "Administrators", "actors": []}
    ]
    with pytest.raises(ValueError, match="At least one user"):
        projects_mixin.remove_project_role_actors("PROJ1", "10002")


def test_get_project_role_members(
    projects_mixin: ProjectsMixin, mock_role_members: dict[str, list[dict[str, str]]]
):
    """Test get_project_role_members method."""
    projects_mixin.jira.get_project_actors_for_role_project.return_value = (
        mock_role_members
    )

    result = projects_mixin.get_project_role_members("PROJ1", "10001")
    assert result == mock_role_members["actors"]
    projects_mixin.jira.get_project_actors_for_role_project.assert_called_once_with(
        project_key="PROJ1", role_id="10001"
    )


def test_get_project_role_members_exception(projects_mixin: ProjectsMixin):
    """Test get_project_role_members method with exception."""
    projects_mixin.jira.get_project_actors_for_role_project.side_effect = Exception(
        "API error"
    )

    result = projects_mixin.get_project_role_members("PROJ1", "10001")
    assert result == []
    projects_mixin.jira.get_project_actors_for_role_project.assert_called_once()


def test_get_project_role_members_invalid_response(projects_mixin: ProjectsMixin):
    """Test get_project_role_members method with invalid response."""
    # Response without actors
    projects_mixin.jira.get_project_actors_for_role_project.return_value = {}

    result = projects_mixin.get_project_role_members("PROJ1", "10001")
    assert result == []
    projects_mixin.jira.get_project_actors_for_role_project.assert_called_once()

    # Non-dict response
    projects_mixin.jira.get_project_actors_for_role_project.reset_mock()
    projects_mixin.jira.get_project_actors_for_role_project.return_value = "not a dict"

    result = projects_mixin.get_project_role_members("PROJ1", "10001")
    assert result == []


def test_get_project_permission_scheme(projects_mixin: ProjectsMixin):
    """Test get_project_permission_scheme method."""
    scheme = {"id": "10000", "name": "Default Permission Scheme"}
    projects_mixin.jira.get_project_permission_scheme.return_value = scheme

    result = projects_mixin.get_project_permission_scheme("PROJ1")
    assert result == scheme
    projects_mixin.jira.get_project_permission_scheme.assert_called_once_with(
        project_id_or_key="PROJ1"
    )


def test_get_project_permission_scheme_exception(projects_mixin: ProjectsMixin):
    """Test get_project_permission_scheme method with exception."""
    projects_mixin.jira.get_project_permission_scheme.side_effect = Exception(
        "API error"
    )

    result = projects_mixin.get_project_permission_scheme("PROJ1")
    assert result is None
    projects_mixin.jira.get_project_permission_scheme.assert_called_once()


def test_get_project_notification_scheme(projects_mixin: ProjectsMixin):
    """Test get_project_notification_scheme method."""
    scheme = {"id": "10000", "name": "Default Notification Scheme"}
    projects_mixin.jira.get_project_notification_scheme.return_value = scheme

    result = projects_mixin.get_project_notification_scheme("PROJ1")
    assert result == scheme
    projects_mixin.jira.get_project_notification_scheme.assert_called_once_with(
        project_id_or_key="PROJ1"
    )


def test_get_project_notification_scheme_exception(projects_mixin: ProjectsMixin):
    """Test get_project_notification_scheme method with exception."""
    projects_mixin.jira.get_project_notification_scheme.side_effect = Exception(
        "API error"
    )

    result = projects_mixin.get_project_notification_scheme("PROJ1")
    assert result is None
    projects_mixin.jira.get_project_notification_scheme.assert_called_once()


def test_get_project_issue_types(
    projects_mixin: ProjectsMixin, mock_issue_types: list[dict]
):
    """Test get_project_issue_types method."""
    projects_mixin.jira.get.side_effect = [
        {"id": "10001"},
        mock_issue_types,
    ]

    result = projects_mixin.get_project_issue_types("PROJ1")
    assert result == mock_issue_types
    assert projects_mixin.jira.get.call_args_list == [
        call("rest/api/3/project/PROJ1"),
        call(
            "rest/api/3/issuetype/project",
            params={"projectId": "10001"},
        ),
    ]


def test_get_workflow_scheme_for_project(projects_mixin: ProjectsMixin):
    """Test resolving a project's workflow scheme through its numeric ID."""
    scheme = {"values": [{"workflowScheme": {"id": 10055}}]}
    projects_mixin.jira.get.side_effect = [{"id": "10001"}, scheme]

    result = projects_mixin.get_workflow_scheme_for_project("PROJ1")

    assert result == scheme
    assert projects_mixin.jira.get.call_args_list == [
        call("rest/api/3/project/PROJ1"),
        call(
            "rest/api/3/workflowscheme/project",
            params={"projectId": "10001"},
        ),
    ]


def test_get_workflow_scheme_for_project_invalid_project(
    projects_mixin: ProjectsMixin,
):
    """Test that an invalid project response is rejected."""
    projects_mixin.jira.get.return_value = {}

    with pytest.raises(TypeError, match="Unexpected project response"):
        projects_mixin.get_workflow_scheme_for_project("PROJ1")


def test_get_project_issue_types_empty_response(projects_mixin: ProjectsMixin):
    """Test get_project_issue_types method with empty response."""
    projects_mixin.jira.get.side_effect = [{"id": "10001"}, []]

    result = projects_mixin.get_project_issue_types("PROJ1")
    assert result == []
    assert projects_mixin.jira.get.call_count == 2


def test_get_project_issue_types_exception(projects_mixin: ProjectsMixin):
    """Test get_project_issue_types method with exception."""
    projects_mixin.jira.get.side_effect = Exception("API error")

    result = projects_mixin.get_project_issue_types("PROJ1")
    assert result == []
    projects_mixin.jira.get.assert_called_once()


def test_get_project_issues_count(projects_mixin: ProjectsMixin):
    """Test get_project_issues_count method."""
    jql_result = {"total": 42}
    projects_mixin.jira.jql.return_value = jql_result

    result = projects_mixin.get_project_issues_count("PROJ1")
    assert result == 42
    projects_mixin.jira.jql.assert_called_once_with(
        jql='project = "PROJ1"', fields="key", limit=1
    )


def test_get_project_issues_count__project_with_reserved_keyword(
    projects_mixin: ProjectsMixin,
):
    """Test get_project_issues_count method."""
    jql_result = {"total": 42}
    projects_mixin.jira.jql.return_value = jql_result

    result = projects_mixin.get_project_issues_count("AND")
    assert result == 42
    projects_mixin.jira.jql.assert_called_once_with(
        jql='project = "AND"', fields="key", limit=1
    )


def test_get_project_issues_count_invalid_response(projects_mixin: ProjectsMixin):
    """Test get_project_issues_count method with invalid response."""
    # No total field
    projects_mixin.jira.jql.return_value = {}

    result = projects_mixin.get_project_issues_count("PROJ1")
    assert result == 0
    projects_mixin.jira.jql.assert_called_once()

    # Non-dict response
    projects_mixin.jira.jql.reset_mock()
    projects_mixin.jira.jql.return_value = "not a dict"

    result = projects_mixin.get_project_issues_count("PROJ1")
    assert result == 0
    projects_mixin.jira.jql.assert_called_once()


def test_get_project_issues_count_exception(projects_mixin: ProjectsMixin):
    """Test get_project_issues_count method with exception."""
    projects_mixin.jira.jql.side_effect = Exception("API error")

    result = projects_mixin.get_project_issues_count("PROJ1")
    assert result == 0
    projects_mixin.jira.jql.assert_called_once()


def test_get_project_issues_with_search_mixin(projects_mixin: ProjectsMixin):
    """Test get_project_issues method with search_issues available."""
    # Mock the search_issues method
    mock_search_result = [MagicMock(), MagicMock()]
    projects_mixin.search_issues = MagicMock(return_value=mock_search_result)

    result = projects_mixin.get_project_issues("PROJ1", start=10, limit=20)
    assert result == mock_search_result
    projects_mixin.search_issues.assert_called_once_with(
        'project = "PROJ1"', start=10, limit=20
    )
    projects_mixin.jira.jql.assert_not_called()


def test_get_project_issues_invalid_response(projects_mixin: ProjectsMixin):
    """Test get_project_issues method with invalid response."""

    # Mock search_issues to simulate an empty result scenario
    mock_search_result = JiraSearchResult(
        issues=[], total=0, start_at=0, max_results=50
    )
    projects_mixin.search_issues = MagicMock(return_value=mock_search_result)

    result = projects_mixin.get_project_issues("PROJ1")
    assert result.issues == []
    projects_mixin.search_issues.assert_called_once()

    # Reset mock and test with non-JiraSearchResult response (this would be handled by the except block)
    projects_mixin.search_issues.reset_mock()
    projects_mixin.search_issues = MagicMock(
        side_effect=TypeError("Not a JiraSearchResult")
    )

    result = projects_mixin.get_project_issues("PROJ1")
    assert result.issues == []
    projects_mixin.search_issues.assert_called_once()


def test_get_project_issues_exception(projects_mixin: ProjectsMixin):
    """Test get_project_issues method with exception."""

    # Mock search_issues to raise an exception, simulating an API error during the search
    projects_mixin.search_issues = MagicMock(side_effect=Exception("API error"))

    result = projects_mixin.get_project_issues("PROJ1")
    assert result.issues == []
    # Verify that search_issues was called, even though it raised an exception
    # The except block in get_project_issues catches it and returns an empty result
    projects_mixin.search_issues.assert_called_once()


def test_get_project_keys(
    projects_mixin: ProjectsMixin, mock_projects: list[dict[str, Any]]
):
    """Test get_project_keys method."""
    # Mock the get_all_projects method
    with patch.object(projects_mixin, "get_all_projects", return_value=mock_projects):
        result = projects_mixin.get_project_keys()
        assert result == ["PROJ1", "PROJ2"]
        projects_mixin.get_all_projects.assert_called_once()


def test_get_project_keys_exception(projects_mixin: ProjectsMixin):
    """Test get_project_keys method with exception."""
    # Mock the get_all_projects method to raise an exception
    with patch.object(
        projects_mixin, "get_all_projects", side_effect=Exception("Error")
    ):
        result = projects_mixin.get_project_keys()
        assert result == []
        projects_mixin.get_all_projects.assert_called_once()


def test_get_project_leads(
    projects_mixin: ProjectsMixin, mock_projects: list[dict[str, Any]]
):
    """Test get_project_leads method."""
    # Mock the get_all_projects method
    with patch.object(projects_mixin, "get_all_projects", return_value=mock_projects):
        result = projects_mixin.get_project_leads()
        assert result == {"PROJ1": "user1", "PROJ2": "user2"}
        projects_mixin.get_all_projects.assert_called_once()


def test_get_project_leads_with_different_lead_formats(
    projects_mixin: ProjectsMixin, mock_projects: list[dict[str, Any]]
):
    """Test get_project_leads method with different lead formats."""
    mixed_projects = [
        # Project with lead as dictionary with name
        {"key": "PROJ1", "lead": {"name": "user1", "displayName": "User One"}},
        # Project with lead as dictionary with displayName but no name
        {"key": "PROJ2", "lead": {"displayName": "User Two"}},
        # Project with lead as string
        {"key": "PROJ3", "lead": "user3"},
        # Project without lead
        {"key": "PROJ4"},
    ]

    # Mock the get_all_projects method
    with patch.object(projects_mixin, "get_all_projects", return_value=mixed_projects):
        result = projects_mixin.get_project_leads()
        assert result == {"PROJ1": "user1", "PROJ2": "User Two", "PROJ3": "user3"}
        projects_mixin.get_all_projects.assert_called_once()


def test_get_project_leads_exception(projects_mixin: ProjectsMixin):
    """Test get_project_leads method with exception."""
    # Mock the get_all_projects method to raise an exception
    with patch.object(
        projects_mixin, "get_all_projects", side_effect=Exception("Error")
    ):
        result = projects_mixin.get_project_leads()
        assert result == {}
        projects_mixin.get_all_projects.assert_called_once()


def test_get_user_accessible_projects(
    projects_mixin: ProjectsMixin, mock_projects: list[dict[str, Any]]
):
    """Test get_user_accessible_projects method."""
    # Mock the get_all_projects method
    with patch.object(projects_mixin, "get_all_projects", return_value=mock_projects):
        # Set up the browse permission responses
        browse_users_responses = [
            [{"name": "test_user"}],  # User has access to PROJ1
            [],  # User doesn't have access to PROJ2
        ]
        projects_mixin.jira.get_users_with_browse_permission_to_a_project.side_effect = browse_users_responses

        result = projects_mixin.get_user_accessible_projects("test_user")

        # Only PROJ1 should be in the result
        assert len(result) == 1
        assert result[0]["key"] == "PROJ1"

        # Check that get_users_with_browse_permission_to_a_project was called for both projects
        assert (
            projects_mixin.jira.get_users_with_browse_permission_to_a_project.call_count
            == 2
        )
        projects_mixin.jira.get_users_with_browse_permission_to_a_project.assert_has_calls(
            [
                call(username="test_user", project_key="PROJ1", limit=1),
                call(username="test_user", project_key="PROJ2", limit=1),
            ]
        )


def test_get_user_accessible_projects_with_permissions_exception(
    projects_mixin: ProjectsMixin, mock_projects: list[dict[str, Any]]
):
    """Test get_user_accessible_projects method with exception in permissions check."""
    # Mock the get_all_projects method
    with patch.object(projects_mixin, "get_all_projects", return_value=mock_projects):
        # First call succeeds, second call raises exception
        projects_mixin.jira.get_users_with_browse_permission_to_a_project.side_effect = [
            [{"name": "test_user"}],  # User has access to PROJ1
            Exception("Permission error"),  # Error checking PROJ2
        ]

        result = projects_mixin.get_user_accessible_projects("test_user")

        # Only PROJ1 should be in the result (PROJ2 was skipped due to error)
        assert len(result) == 1
        assert result[0]["key"] == "PROJ1"


def test_get_user_accessible_projects_exception(projects_mixin: ProjectsMixin):
    """Test get_user_accessible_projects method with main exception."""
    # Mock the get_all_projects method to raise an exception
    with patch.object(
        projects_mixin, "get_all_projects", side_effect=Exception("Error")
    ):
        result = projects_mixin.get_user_accessible_projects("test_user")
        assert result == []
        projects_mixin.get_all_projects.assert_called_once()
        projects_mixin.jira.get_users_with_browse_permission_to_a_project.assert_not_called()


def test_create_project_version_minimal(projects_mixin: ProjectsMixin) -> None:
    """Test create_project_version with only required fields."""
    mock_response = {"id": "201", "name": "v4.0"}
    with patch.object(
        projects_mixin, "create_version", return_value=mock_response
    ) as mock_create_version:
        result = projects_mixin.create_project_version(project_key="PROJ2", name="v4.0")
        assert result == mock_response
        mock_create_version.assert_called_once_with(
            project="PROJ2",
            name="v4.0",
            start_date=None,
            release_date=None,
            description=None,
        )


def test_create_project_version_all_fields(projects_mixin: ProjectsMixin) -> None:
    """Test create_project_version with all fields."""
    mock_response = {
        "id": "202",
        "name": "v5.0",
        "description": "Release 5.0",
        "startDate": "2025-08-01",
        "releaseDate": "2025-08-15",
    }
    with patch.object(
        projects_mixin, "create_version", return_value=mock_response
    ) as mock_create_version:
        result = projects_mixin.create_project_version(
            project_key="PROJ3",
            name="v5.0",
            start_date="2025-08-01",
            release_date="2025-08-15",
            description="Release 5.0",
        )
        assert result == mock_response
        mock_create_version.assert_called_once_with(
            project="PROJ3",
            name="v5.0",
            start_date="2025-08-01",
            release_date="2025-08-15",
            description="Release 5.0",
        )


def test_create_project_version_error(projects_mixin: ProjectsMixin) -> None:
    """Test create_project_version propagates errors."""
    with patch.object(
        projects_mixin, "create_version", side_effect=Exception("API failure")
    ):
        with pytest.raises(Exception):
            projects_mixin.create_project_version("PROJ4", "v6.0")


def test_get_project_properties_list_keys(projects_mixin: ProjectsMixin) -> None:
    """Test get_project_properties listing keys when property_key is None."""
    projects_mixin.jira.get.return_value = {
        "keys": [
            {"key": "pbrr-settings", "self": "http://example.com/prop1"},
            {"key": "jira.global.components", "self": "http://example.com/prop2"},
        ]
    }
    result = projects_mixin.get_project_properties("SST")
    assert result["project"] == "SST"
    assert result["keys"] == ["pbrr-settings", "jira.global.components"]
    assert result["total_keys"] == 2
    projects_mixin.jira.get.assert_called_once_with("rest/api/3/project/SST/properties")


def test_get_project_properties_get_value(projects_mixin: ProjectsMixin) -> None:
    """Test get_project_properties returning value for a key."""
    projects_mixin.jira.get.return_value = {
        "key": "pbrr-settings",
        "value": {"issueTypeId": "10062", "suppRiskTypes": ["10017"]},
    }
    result = projects_mixin.get_project_properties("SST", property_key="pbrr-settings")
    assert result["project"] == "SST"
    assert result["key"] == "pbrr-settings"
    assert result["value"] == {"issueTypeId": "10062", "suppRiskTypes": ["10017"]}
    assert result["truncated"] is False


def test_get_project_properties_truncated(projects_mixin: ProjectsMixin) -> None:
    """Test get_project_properties truncating large value."""
    projects_mixin.jira.get.return_value = {
        "key": "large-key",
        "value": {"data": "x" * 200},
    }
    result = projects_mixin.get_project_properties(
        "SST", property_key="large-key", max_length=50
    )
    assert result["truncated"] is True
    assert "_truncated_preview" in result["value"]


def test_get_project_properties_404(projects_mixin: ProjectsMixin) -> None:
    """Test get_project_properties 404 raises clear ValueError."""
    from requests.exceptions import HTTPError
    from requests.models import Response

    mock_resp = Response()
    mock_resp.status_code = 404
    error = HTTPError("Not Found", response=mock_resp)
    projects_mixin.jira.get.side_effect = error

    with pytest.raises(ValueError, match="Ikke funnet"):
        projects_mixin.get_project_properties("NONEXIST")


def test_get_project_properties_403(projects_mixin: ProjectsMixin) -> None:
    """Test get_project_properties 403 raises clear PermissionError."""
    from requests.exceptions import HTTPError
    from requests.models import Response

    mock_resp = Response()
    mock_resp.status_code = 403
    error = HTTPError("Forbidden", response=mock_resp)
    projects_mixin.jira.get.side_effect = error

    with pytest.raises(PermissionError, match="Ikke tillatt for denne brukeren"):
        projects_mixin.get_project_properties("FORBIDDEN")
