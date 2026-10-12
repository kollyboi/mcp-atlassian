"""Jira API module for mcp_atlassian.

This module provides various Jira API client implementations.
"""

# flake8: noqa

# Re-export the Jira class for backward compatibility
from atlassian.jira import Jira

from .client import JiraClient
from .comments import CommentsMixin
from .config import JiraConfig
from .epics import EpicsMixin
from .fields import FieldsMixin
from .formatting import FormattingMixin
from .issues import IssuesMixin
from .links import LinksMixin
from .projects import ProjectsMixin
from .search import SearchMixin
from .sprints import SprintsMixin
from .transitions import TransitionsMixin
from .users import UsersMixin
from .worklog import WorklogMixin
from .boards import BoardsMixin
from .attachments import AttachmentsMixin
from .org_admin import OrgAdminMixin
from .screens import ScreensMixin
from .security import SecurityMixin
from .statuses import StatusesMixin
from .workflows import WorkflowsMixin
from .automation import AutomationMixin
from .filters import FiltersMixin
from .dashboards import DashboardsMixin


class JiraFetcher(
    ProjectsMixin,
    FieldsMixin,
    FormattingMixin,
    TransitionsMixin,
    WorklogMixin,
    EpicsMixin,
    CommentsMixin,
    SearchMixin,
    IssuesMixin,
    UsersMixin,
    BoardsMixin,
    SprintsMixin,
    AttachmentsMixin,
    LinksMixin,
    OrgAdminMixin,
    ScreensMixin,
    SecurityMixin,
    StatusesMixin,
    WorkflowsMixin,
    AutomationMixin,
    FiltersMixin,
    DashboardsMixin,
):
    """
    The main Jira client class providing access to all Jira operations.

    This class inherits from multiple mixins that provide specific functionality:
    - ProjectsMixin: Project-related operations
    - FieldsMixin: Field-related operations
    - FormattingMixin: Content formatting utilities
    - TransitionsMixin: Issue transition operations
    - WorklogMixin: Worklog operations
    - EpicsMixin: Epic operations
    - CommentsMixin: Comment operations
    - SearchMixin: Search operations
    - IssuesMixin: Issue operations
    - UsersMixin: User operations
    - BoardsMixin: Board operations
    - SprintsMixin: Sprint operations
    - AttachmentsMixin: Attachment download operations
    - LinksMixin: Issue link operations
    - OrgAdminMixin: Organization admin operations (requires JIRA_ORG_ID + JIRA_ORG_KEY)
    - ScreensMixin: Screen, field context and field configuration admin operations
    - StatusesMixin: Status admin operations
    - WorkflowsMixin: Workflow admin operations
    - AutomationMixin: Automation rule management operations
    - FiltersMixin: Filter (saved JQL search) operations
    - DashboardsMixin: Dashboard and gadget operations

    The class structure is designed to maintain backward compatibility while
    improving code organization and maintainability.
    """

    pass


__all__ = ["JiraFetcher", "JiraConfig", "JiraClient", "Jira"]
