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
        json={"statuses": [{"name": "Approved", "statusCategory": "DONE"}]},
    )


def test_workflow_operations(mixin):
    mixin.jira.get.return_value = {"values": [{"name": "Application"}]}
    assert mixin.search_workflows("Application")["values"]

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


def test_admin_operations_require_payload(mixin):
    with pytest.raises(ValueError):
        mixin.create_statuses([])
    with pytest.raises(ValueError):
        mixin.update_workflows([])
