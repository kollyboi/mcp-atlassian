"""Tests for Jira status and workflow administration mixins."""

from unittest.mock import patch

import pytest

from mcp_atlassian.jira import JiraFetcher


@pytest.fixture
def mixin(mock_config, mock_atlassian_jira):
    with patch("atlassian.Jira") as cls:
        cls.return_value = mock_atlassian_jira
        fetcher = JiraFetcher(config=mock_config)
        fetcher.jira = mock_atlassian_jira
        yield fetcher


def test_status_operations(mixin):
    mixin.jira.get.return_value = {"values": [{"id": "1", "name": "Open"}]}
    assert mixin.search_statuses("Open")["values"][0]["name"] == "Open"
    mixin.jira.get.assert_called_once_with(
        "rest/api/3/statuses/search",
        params={"startAt": 0, "maxResults": 50, "query": "Open"},
    )

    mixin.jira.post.return_value = [{"id": "2", "name": "Approved"}]
    assert mixin.create_statuses([{"name": "Approved", "statusCategory": "DONE"}])
    mixin.jira.post.assert_called_once_with(
        "rest/api/3/statuses",
        json={
            "scope": {"type": "GLOBAL"},
            "statuses": [{"name": "Approved", "statusCategory": "DONE"}],
        },
    )


def test_create_statuses_sends_scope_at_top_level(mixin):
    """Regression: Jira rejects the payload without a top-level scope."""
    mixin.jira.post.return_value = [{"id": "3", "name": "Approved"}]
    scope = {"type": "PROJECT", "project": {"id": "10001"}}

    mixin.create_statuses([{"name": "Approved", "statusCategory": "DONE"}], scope)

    mixin.jira.post.assert_called_once_with(
        "rest/api/3/statuses",
        json={
            "scope": scope,
            "statuses": [{"name": "Approved", "statusCategory": "DONE"}],
        },
    )


def test_workflow_operations(mixin):
    mixin.jira.get.return_value = {"values": [{"name": "Application"}]}
    assert mixin.search_workflows("Application")["values"]
    mixin.jira.get.assert_called_with(
        "rest/api/3/workflows/search",
        params={"startAt": 0, "maxResults": 50, "queryString": "Application"},
    )

    mixin.jira.post.return_value = {"taskId": "1"}
    assert mixin.update_workflows([{"name": "Application"}]) == {"taskId": "1"}
    mixin.jira.post.assert_called_with(
        "rest/api/3/workflows/update", json={"workflows": [{"name": "Application"}]}
    )

    mixin.publish_workflow_scheme("10", [{"statusId": "1", "newStatusId": "2"}])
    mixin.jira.post.assert_called_with(
        "rest/api/3/workflowscheme/10/draft/publish",
        json={"statusMappings": [{"statusId": "1", "newStatusId": "2"}]},
    )


def test_search_workflows_partial_name_still_matches(mixin):
    """Regression: the legacy endpoint matched only exact workflow names.

    A partial name has to be sent as queryString to the /workflows/search
    endpoint for Jira to return any match.
    """
    mixin.jira.get.return_value = {"values": [{"name": "BB5 - ICE workflow"}]}

    assert mixin.search_workflows("BB5 - ICE")["values"][0]["name"] == (
        "BB5 - ICE workflow"
    )
    mixin.jira.get.assert_called_once_with(
        "rest/api/3/workflows/search",
        params={"startAt": 0, "maxResults": 50, "queryString": "BB5 - ICE"},
    )


def test_update_workflows_sends_statuses_at_top_level(mixin):
    """Regression: Jira's WorkflowUpdateRequest takes statuses next to workflows."""
    mixin.jira.post.return_value = {"taskId": "1"}
    workflows = [{"id": "w1", "version": {"versionNumber": 5}}]
    statuses = [
        {
            "id": "10361",
            "name": "New",
            "statusCategory": "IN_PROGRESS",
            "statusReference": "10361",
        }
    ]

    mixin.update_workflows(workflows, statuses)

    mixin.jira.post.assert_called_once_with(
        "rest/api/3/workflows/update",
        json={"workflows": workflows, "statuses": statuses},
    )


def test_validate_update_workflows_wraps_payload(mixin):
    """The validation endpoint wants the update request nested under payload."""
    mixin.jira.post.return_value = {"errors": []}
    workflows = [{"id": "w1"}]
    statuses = [{"name": "New", "statusCategory": "TODO", "statusReference": "1"}]

    assert mixin.validate_update_workflows(workflows, statuses, ["ERROR"]) == {
        "errors": []
    }

    mixin.jira.post.assert_called_once_with(
        "rest/api/3/workflows/update/validation",
        json={
            "payload": {"workflows": workflows, "statuses": statuses},
            "validationOptions": {"levels": ["ERROR"]},
        },
    )


def test_validate_update_workflows_omits_optional_parts(mixin):
    mixin.jira.post.return_value = {"errors": []}

    mixin.validate_update_workflows([{"id": "w1"}])

    mixin.jira.post.assert_called_once_with(
        "rest/api/3/workflows/update/validation",
        json={"payload": {"workflows": [{"id": "w1"}]}},
    )


def test_search_workflows_expands_transitions(mixin):
    """Regression: without expand Jira omits transitions from the response.

    The expand parameter has to be forwarded so callers can read transitions
    (and their rules) before building an update payload.
    """
    mixin.jira.get.return_value = {"values": [{"name": "BB5 - Søknad"}]}

    assert mixin.search_workflows("BB5 - Søknad", expand="values.transitions")["values"]
    mixin.jira.get.assert_called_once_with(
        "rest/api/3/workflows/search",
        params={
            "startAt": 0,
            "maxResults": 50,
            "queryString": "BB5 - Søknad",
            "expand": "values.transitions",
        },
    )


def test_search_workflows_omits_expand_when_unset(mixin):
    """No expand value means no expand key in the request params."""
    mixin.jira.get.return_value = {"values": []}

    mixin.search_workflows()
    mixin.jira.get.assert_called_once_with(
        "rest/api/3/workflows/search",
        params={"startAt": 0, "maxResults": 50},
    )


def test_admin_operations_require_payload(mixin):
    with pytest.raises(ValueError):
        mixin.create_statuses([])
    with pytest.raises(ValueError):
        mixin.update_workflows([])
    with pytest.raises(ValueError):
        mixin.validate_update_workflows([])
