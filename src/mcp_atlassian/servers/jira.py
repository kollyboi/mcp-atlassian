"""Jira FastMCP server instance and tool definitions."""

import json
import logging
from typing import Annotated, Any

from fastmcp import Context, FastMCP
from pydantic import Field
from requests.exceptions import HTTPError

from mcp_atlassian.exceptions import MCPAtlassianAuthenticationError
from mcp_atlassian.jira.constants import DEFAULT_READ_JIRA_FIELDS
from mcp_atlassian.models.jira.common import JiraUser
from mcp_atlassian.servers.dependencies import get_jira_fetcher
from mcp_atlassian.utils.decorators import check_write_access

logger = logging.getLogger(__name__)

jira_mcp = FastMCP(
    name="Jira MCP Service",
    instructions="Provides tools for interacting with Atlassian Jira.",
)


@jira_mcp.tool(
    tags={"jira", "read"},
    annotations={"title": "Get User Profile", "readOnlyHint": True},
)
async def get_user_profile(
    ctx: Context,
    user_identifier: Annotated[
        str,
        Field(
            description="Identifier for the user (e.g., email address 'user@example.com', username 'johndoe', account ID 'accountid:...', or key for Server/DC)."
        ),
    ],
) -> str:
    """
    Retrieve profile information for a specific Jira user.

    Args:
        ctx: The FastMCP context.
        user_identifier: User identifier (email, username, key, or account ID).

    Returns:
        JSON string representing the Jira user profile object, or an error object if not found.

    Raises:
        ValueError: If the Jira client is not configured or available.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        user: JiraUser = jira.get_user_profile_by_identifier(user_identifier)
        result = user.to_simplified_dict()
        response_data = {"success": True, "user": result}
    except Exception as e:
        error_message = ""
        log_level = logging.ERROR
        if isinstance(e, ValueError) and "not found" in str(e).lower():
            log_level = logging.WARNING
            error_message = str(e)
        elif isinstance(e, MCPAtlassianAuthenticationError):
            error_message = f"Authentication/Permission Error: {str(e)}"
        elif isinstance(e, OSError | HTTPError):
            error_message = f"Network or API Error: {str(e)}"
        else:
            error_message = (
                "An unexpected error occurred while fetching the user profile."
            )
            logger.exception(
                f"Unexpected error in get_user_profile for '{user_identifier}':"
            )
        error_result = {
            "success": False,
            "error": str(e),
            "user_identifier": user_identifier,
        }
        logger.log(
            log_level,
            f"get_user_profile failed for '{user_identifier}': {error_message}",
        )
        response_data = error_result
    return json.dumps(response_data, indent=2, ensure_ascii=False)


@jira_mcp.tool(
    tags={"jira", "read"},
    annotations={"title": "Search Users", "readOnlyHint": True},
)
async def search_users(
    ctx: Context,
    query: Annotated[
        str, Field(description="Name, email, or account ID to search for.")
    ],
    start_at: Annotated[
        int, Field(description="Starting index for pagination.", default=0, ge=0)
    ] = 0,
    max_results: Annotated[
        int, Field(description="Maximum users to return.", default=50, ge=1, le=100)
    ] = 50,
) -> str:
    """Find Jira users and return account IDs suitable for issue updates."""
    jira = await get_jira_fetcher(ctx)
    users = jira.search_users(query, start_at=start_at, max_results=max_results)
    return json.dumps(users, indent=2, ensure_ascii=False)


@jira_mcp.tool(
    tags={"jira", "read"},
    annotations={"title": "Get Issue", "readOnlyHint": True},
)
async def get_issue(
    ctx: Context,
    issue_key: Annotated[str, Field(description="Jira issue key (e.g., 'PROJ-123')")],
    fields: Annotated[
        str,
        Field(
            description=(
                "(Optional) Comma-separated list of fields to return (e.g., 'summary,status,customfield_10010'). "
                "You may also provide a single field as a string (e.g., 'duedate'). "
                "Use '*all' for all fields (including custom fields), or omit for essential fields only."
            ),
            default=",".join(DEFAULT_READ_JIRA_FIELDS),
        ),
    ] = ",".join(DEFAULT_READ_JIRA_FIELDS),
    expand: Annotated[
        str | None,
        Field(
            description=(
                "(Optional) Fields to expand. Examples: 'renderedFields' (for rendered content), "
                "'transitions' (for available status transitions), 'changelog' (for history)"
            ),
            default=None,
        ),
    ] = None,
    comment_limit: Annotated[
        int,
        Field(
            description="Maximum number of comments to include (0 or null for no comments)",
            default=10,
            ge=0,
            le=100,
        ),
    ] = 10,
    properties: Annotated[
        str | None,
        Field(
            description="(Optional) A comma-separated list of issue properties to return",
            default=None,
        ),
    ] = None,
    update_history: Annotated[
        bool,
        Field(
            description="Whether to update the issue view history for the requesting user",
            default=True,
        ),
    ] = True,
) -> str:
    """Get details of a specific Jira issue including its Epic links and relationship information.

    Args:
        ctx: The FastMCP context.
        issue_key: Jira issue key.
        fields: Comma-separated list of fields to return (e.g., 'summary,status,customfield_10010'), a single field as a string (e.g., 'duedate'), '*all' for all fields, or omitted for essentials.
        expand: Optional fields to expand.
        comment_limit: Maximum number of comments.
        properties: Issue properties to return.
        update_history: Whether to update issue view history.

    Returns:
        JSON string representing the Jira issue object.

    Raises:
        ValueError: If the Jira client is not configured or available.
    """
    jira = await get_jira_fetcher(ctx)
    fields_list: str | list[str] | None = fields
    if fields and fields != "*all":
        fields_list = [f.strip() for f in fields.split(",")]

    issue = jira.get_issue(
        issue_key=issue_key,
        fields=fields_list,
        expand=expand,
        comment_limit=comment_limit,
        properties=properties.split(",") if properties else None,
        update_history=update_history,
    )
    result = issue.to_simplified_dict()
    return json.dumps(result, indent=2, ensure_ascii=False)


@jira_mcp.tool(
    tags={"jira", "read"},
    annotations={"title": "Search Issues", "readOnlyHint": True},
)
async def search(
    ctx: Context,
    jql: Annotated[
        str,
        Field(
            description=(
                "JQL query string (Jira Query Language). Examples:\n"
                '- Find Epics: "issuetype = Epic AND project = PROJ"\n'
                '- Find issues in Epic: "parent = PROJ-123"\n'
                "- Find by status: \"status = 'In Progress' AND project = PROJ\"\n"
                '- Find by assignee: "assignee = currentUser()"\n'
                '- Find recently updated: "updated >= -7d AND project = PROJ"\n'
                '- Find by label: "labels = frontend AND project = PROJ"\n'
                '- Find by priority: "priority = High AND project = PROJ"'
            )
        ),
    ],
    fields: Annotated[
        str,
        Field(
            description=(
                "(Optional) Comma-separated fields to return in the results. "
                "Use '*all' for all fields, or specify individual fields like 'summary,status,assignee,priority'"
            ),
            default=",".join(DEFAULT_READ_JIRA_FIELDS),
        ),
    ] = ",".join(DEFAULT_READ_JIRA_FIELDS),
    limit: Annotated[
        int,
        Field(description="Maximum number of results (1-50)", default=10, ge=1),
    ] = 10,
    start_at: Annotated[
        int,
        Field(description="Starting index for pagination (0-based)", default=0, ge=0),
    ] = 0,
    next_page_token: Annotated[
        str | None,
        Field(
            description=(
                "Opaque next_page_token returned by a previous Cloud search. "
                "Use this instead of start_at for efficient pagination."
            ),
            default=None,
        ),
    ] = None,
    projects_filter: Annotated[
        str | None,
        Field(
            description=(
                "(Optional) Comma-separated list of project keys to filter results by. "
                "Overrides the environment variable JIRA_PROJECTS_FILTER if provided."
            ),
            default=None,
        ),
    ] = None,
    expand: Annotated[
        str | None,
        Field(
            description=(
                "(Optional) fields to expand. Examples: 'renderedFields', 'transitions', 'changelog'"
            ),
            default=None,
        ),
    ] = None,
) -> str:
    """Search Jira issues using JQL (Jira Query Language).

    Args:
        ctx: The FastMCP context.
        jql: JQL query string.
        fields: Comma-separated fields to return.
        limit: Maximum number of results.
        start_at: Starting index for pagination.
        next_page_token: Opaque Cloud continuation token from a previous result.
        projects_filter: Comma-separated list of project keys to filter by.
        expand: Optional fields to expand.

    Returns:
        JSON string representing the search results including pagination info.
    """
    jira = await get_jira_fetcher(ctx)
    fields_list: str | list[str] | None = fields
    if fields and fields != "*all":
        fields_list = [f.strip() for f in fields.split(",")]

    search_kwargs: dict[str, Any] = {
        "jql": jql,
        "fields": fields_list,
        "limit": limit,
        "start": start_at,
        "expand": expand,
        "projects_filter": projects_filter,
    }
    if next_page_token is not None:
        search_kwargs["next_page_token"] = next_page_token

    search_result = jira.search_issues(
        **search_kwargs,
    )
    result = search_result.to_simplified_dict()
    return json.dumps(result, indent=2, ensure_ascii=False)


@jira_mcp.tool(
    tags={"jira", "read"},
    annotations={"title": "Count Issues", "readOnlyHint": True},
)
async def count_issues(
    ctx: Context,
    jql: Annotated[str, Field(description="JQL query string to count.")],
) -> str:
    """Return a compact count for issues matching a JQL query."""
    jira = await get_jira_fetcher(ctx)
    return json.dumps(
        {"jql": jql, "count": jira.count_issues(jql)},
        indent=2,
        ensure_ascii=False,
    )


@jira_mcp.tool(
    tags={"jira", "read"},
    annotations={"title": "Search Fields", "readOnlyHint": True},
)
async def search_fields(
    ctx: Context,
    keyword: Annotated[
        str,
        Field(
            description="Keyword for fuzzy search. If left empty, lists the first 'limit' available fields in their default order.",
            default="",
        ),
    ] = "",
    limit: Annotated[
        int, Field(description="Maximum number of results", default=10, ge=1)
    ] = 10,
    refresh: Annotated[
        bool,
        Field(description="Whether to force refresh the field list", default=False),
    ] = False,
) -> str:
    """Search Jira fields by keyword with fuzzy match.

    Args:
        ctx: The FastMCP context.
        keyword: Keyword for fuzzy search.
        limit: Maximum number of results.
        refresh: Whether to force refresh the field list.

    Returns:
        JSON string representing a list of matching field definitions.
    """
    jira = await get_jira_fetcher(ctx)
    result = jira.search_fields(keyword, limit=limit, refresh=refresh)
    return json.dumps(result, indent=2, ensure_ascii=False)


@jira_mcp.tool(
    tags={"jira", "read"},
    annotations={"title": "Get Project Issues", "readOnlyHint": True},
)
async def get_project_issues(
    ctx: Context,
    project_key: Annotated[str, Field(description="The project key")],
    limit: Annotated[
        int,
        Field(description="Maximum number of results (1-50)", default=10, ge=1, le=50),
    ] = 10,
    start_at: Annotated[
        int,
        Field(description="Starting index for pagination (0-based)", default=0, ge=0),
    ] = 0,
) -> str:
    """Get all issues for a specific Jira project.

    Args:
        ctx: The FastMCP context.
        project_key: The project key.
        limit: Maximum number of results.
        start_at: Starting index for pagination.

    Returns:
        JSON string representing the search results including pagination info.
    """
    jira = await get_jira_fetcher(ctx)
    search_result = jira.get_project_issues(
        project_key=project_key, start=start_at, limit=limit
    )
    result = search_result.to_simplified_dict()
    return json.dumps(result, indent=2, ensure_ascii=False)


@jira_mcp.tool(
    tags={"jira", "read"},
    annotations={"title": "Get Transitions", "readOnlyHint": True},
)
async def get_transitions(
    ctx: Context,
    issue_key: Annotated[str, Field(description="Jira issue key (e.g., 'PROJ-123')")],
) -> str:
    """Get available status transitions for a Jira issue.

    Args:
        ctx: The FastMCP context.
        issue_key: Jira issue key.

    Returns:
        JSON string representing a list of available transitions.
    """
    jira = await get_jira_fetcher(ctx)
    # Underlying method returns list[dict] in the desired format
    transitions = jira.get_available_transitions(issue_key)
    return json.dumps(transitions, indent=2, ensure_ascii=False)


@jira_mcp.tool(
    tags={"jira", "read"},
    annotations={"title": "Get Worklog", "readOnlyHint": True},
)
async def get_worklog(
    ctx: Context,
    issue_key: Annotated[str, Field(description="Jira issue key (e.g., 'PROJ-123')")],
) -> str:
    """Get worklog entries for a Jira issue.

    Args:
        ctx: The FastMCP context.
        issue_key: Jira issue key.

    Returns:
        JSON string representing the worklog entries.
    """
    jira = await get_jira_fetcher(ctx)
    worklogs = jira.get_worklogs(issue_key)
    result = {"worklogs": worklogs}
    return json.dumps(result, indent=2, ensure_ascii=False)


@jira_mcp.tool(
    tags={"jira", "read"},
    annotations={"title": "Download Attachments", "readOnlyHint": True},
)
async def download_attachments(
    ctx: Context,
    issue_key: Annotated[str, Field(description="Jira issue key (e.g., 'PROJ-123')")],
    target_dir: Annotated[
        str, Field(description="Directory where attachments should be saved")
    ],
) -> str:
    """Download attachments from a Jira issue.

    Args:
        ctx: The FastMCP context.
        issue_key: Jira issue key.
        target_dir: Directory to save attachments.

    Returns:
        JSON string indicating the result of the download operation.
    """
    jira = await get_jira_fetcher(ctx)
    result = jira.download_issue_attachments(issue_key=issue_key, target_dir=target_dir)
    return json.dumps(result, indent=2, ensure_ascii=False)


@jira_mcp.tool(
    tags={"jira", "read"},
    annotations={"title": "Get Agile Boards", "readOnlyHint": True},
)
async def get_agile_boards(
    ctx: Context,
    board_name: Annotated[
        str | None,
        Field(description="(Optional) The name of board, support fuzzy search"),
    ] = None,
    project_key: Annotated[
        str | None, Field(description="(Optional) Jira project key (e.g., 'PROJ-123')")
    ] = None,
    board_type: Annotated[
        str | None,
        Field(
            description="(Optional) The type of jira board (e.g., 'scrum', 'kanban')"
        ),
    ] = None,
    start_at: Annotated[
        int,
        Field(description="Starting index for pagination (0-based)", default=0, ge=0),
    ] = 0,
    limit: Annotated[
        int,
        Field(description="Maximum number of results (1-50)", default=10, ge=1, le=50),
    ] = 10,
) -> str:
    """Get jira agile boards by name, project key, or type.

    Args:
        ctx: The FastMCP context.
        board_name: Name of the board (fuzzy search).
        project_key: Project key.
        board_type: Board type ('scrum' or 'kanban').
        start_at: Starting index.
        limit: Maximum results.

    Returns:
        JSON string representing a list of board objects.
    """
    jira = await get_jira_fetcher(ctx)
    boards = jira.get_all_agile_boards_model(
        board_name=board_name,
        project_key=project_key,
        board_type=board_type,
        start=start_at,
        limit=limit,
    )
    result = [board.to_simplified_dict() for board in boards]
    return json.dumps(result, indent=2, ensure_ascii=False)


@jira_mcp.tool(
    tags={"jira", "read"},
    annotations={"title": "Get Board Issues", "readOnlyHint": True},
)
async def get_board_issues(
    ctx: Context,
    board_id: Annotated[str, Field(description="The id of the board (e.g., '1001')")],
    jql: Annotated[
        str,
        Field(
            description=(
                "JQL query string (Jira Query Language). Examples:\n"
                '- Find Epics: "issuetype = Epic AND project = PROJ"\n'
                '- Find issues in Epic: "parent = PROJ-123"\n'
                "- Find by status: \"status = 'In Progress' AND project = PROJ\"\n"
                '- Find by assignee: "assignee = currentUser()"\n'
                '- Find recently updated: "updated >= -7d AND project = PROJ"\n'
                '- Find by label: "labels = frontend AND project = PROJ"\n'
                '- Find by priority: "priority = High AND project = PROJ"'
            )
        ),
    ],
    fields: Annotated[
        str,
        Field(
            description=(
                "Comma-separated fields to return in the results. "
                "Use '*all' for all fields, or specify individual "
                "fields like 'summary,status,assignee,priority'"
            ),
            default=",".join(DEFAULT_READ_JIRA_FIELDS),
        ),
    ] = ",".join(DEFAULT_READ_JIRA_FIELDS),
    start_at: Annotated[
        int,
        Field(description="Starting index for pagination (0-based)", default=0, ge=0),
    ] = 0,
    limit: Annotated[
        int,
        Field(description="Maximum number of results (1-50)", default=10, ge=1, le=50),
    ] = 10,
    expand: Annotated[
        str,
        Field(
            description="Optional fields to expand in the response (e.g., 'changelog').",
            default="version",
        ),
    ] = "version",
) -> str:
    """Get all issues linked to a specific board filtered by JQL.

    Args:
        ctx: The FastMCP context.
        board_id: The ID of the board.
        jql: JQL query string to filter issues.
        fields: Comma-separated fields to return.
        start_at: Starting index for pagination.
        limit: Maximum number of results.
        expand: Optional fields to expand.

    Returns:
        JSON string representing the search results including pagination info.
    """
    jira = await get_jira_fetcher(ctx)
    fields_list: str | list[str] | None = fields
    if fields and fields != "*all":
        fields_list = [f.strip() for f in fields.split(",")]

    search_result = jira.get_board_issues(
        board_id=board_id,
        jql=jql,
        fields=fields_list,
        start=start_at,
        limit=limit,
        expand=expand,
    )
    result = search_result.to_simplified_dict()
    return json.dumps(result, indent=2, ensure_ascii=False)


@jira_mcp.tool(
    tags={"jira", "read"},
    annotations={"title": "Get Sprints from Board", "readOnlyHint": True},
)
async def get_sprints_from_board(
    ctx: Context,
    board_id: Annotated[str, Field(description="The id of board (e.g., '1000')")],
    state: Annotated[
        str | None,
        Field(description="Sprint state (e.g., 'active', 'future', 'closed')"),
    ] = None,
    start_at: Annotated[
        int,
        Field(description="Starting index for pagination (0-based)", default=0, ge=0),
    ] = 0,
    limit: Annotated[
        int,
        Field(description="Maximum number of results (1-50)", default=10, ge=1, le=50),
    ] = 10,
) -> str:
    """Get jira sprints from board by state.

    Args:
        ctx: The FastMCP context.
        board_id: The ID of the board.
        state: Sprint state ('active', 'future', 'closed'). If None, returns all sprints.
        start_at: Starting index.
        limit: Maximum results.

    Returns:
        JSON string representing a list of sprint objects.
    """
    jira = await get_jira_fetcher(ctx)
    sprints = jira.get_all_sprints_from_board_model(
        board_id=board_id, state=state, start=start_at, limit=limit
    )
    result = [sprint.to_simplified_dict() for sprint in sprints]
    return json.dumps(result, indent=2, ensure_ascii=False)


@jira_mcp.tool(
    tags={"jira", "read"},
    annotations={"title": "Get Sprint Issues", "readOnlyHint": True},
)
async def get_sprint_issues(
    ctx: Context,
    sprint_id: Annotated[str, Field(description="The id of sprint (e.g., '10001')")],
    fields: Annotated[
        str,
        Field(
            description=(
                "Comma-separated fields to return in the results. "
                "Use '*all' for all fields, or specify individual "
                "fields like 'summary,status,assignee,priority'"
            ),
            default=",".join(DEFAULT_READ_JIRA_FIELDS),
        ),
    ] = ",".join(DEFAULT_READ_JIRA_FIELDS),
    start_at: Annotated[
        int,
        Field(description="Starting index for pagination (0-based)", default=0, ge=0),
    ] = 0,
    limit: Annotated[
        int,
        Field(description="Maximum number of results (1-50)", default=10, ge=1, le=50),
    ] = 10,
) -> str:
    """Get jira issues from sprint.

    Args:
        ctx: The FastMCP context.
        sprint_id: The ID of the sprint.
        fields: Comma-separated fields to return.
        start_at: Starting index.
        limit: Maximum results.

    Returns:
        JSON string representing the search results including pagination info.
    """
    jira = await get_jira_fetcher(ctx)
    fields_list: str | list[str] | None = fields
    if fields and fields != "*all":
        fields_list = [f.strip() for f in fields.split(",")]

    search_result = jira.get_sprint_issues(
        sprint_id=sprint_id, fields=fields_list, start=start_at, limit=limit
    )
    result = search_result.to_simplified_dict()
    return json.dumps(result, indent=2, ensure_ascii=False)


@jira_mcp.tool(
    tags={"jira", "read"},
    annotations={"title": "Get Link Types", "readOnlyHint": True},
)
async def get_link_types(ctx: Context) -> str:
    """Get all available issue link types.

    Args:
        ctx: The FastMCP context.

    Returns:
        JSON string representing a list of issue link type objects.
    """
    jira = await get_jira_fetcher(ctx)
    link_types = jira.get_issue_link_types()
    formatted_link_types = [link_type.to_simplified_dict() for link_type in link_types]
    return json.dumps(formatted_link_types, indent=2, ensure_ascii=False)


@jira_mcp.tool(
    tags={"jira", "write"},
    annotations={"title": "Create Issue", "destructiveHint": True},
)
@check_write_access
async def create_issue(
    ctx: Context,
    project_key: Annotated[
        str,
        Field(
            description=(
                "The JIRA project key (e.g. 'PROJ', 'DEV', 'SUPPORT'). "
                "This is the prefix of issue keys in your project. "
                "Never assume what it might be, always ask the user."
            )
        ),
    ],
    summary: Annotated[str, Field(description="Summary/title of the issue")],
    issue_type: Annotated[
        str,
        Field(
            description=(
                "Issue type (e.g. 'Task', 'Bug', 'Story', 'Epic', 'Subtask'). "
                "The available types depend on your project configuration. "
                "For subtasks, use 'Subtask' (not 'Sub-task') and include parent in additional_fields."
            ),
        ),
    ],
    assignee: Annotated[
        str | None,
        Field(
            description="(Optional) Assignee's user identifier (string): Email, display name, or account ID (e.g., 'user@example.com', 'John Doe', 'accountid:...')",
            default=None,
        ),
    ] = None,
    description: Annotated[
        str | None, Field(description="Issue description", default=None)
    ] = None,
    components: Annotated[
        str | None,
        Field(
            description="(Optional) Comma-separated list of component names to assign (e.g., 'Frontend,API')",
            default=None,
        ),
    ] = None,
    additional_fields: Annotated[
        dict[str, Any] | str | None,
        Field(
            description=(
                "(Optional) Dictionary of additional fields to set. Examples:\n"
                "- Set priority: {'priority': {'name': 'High'}}\n"
                "- Add labels: {'labels': ['frontend', 'urgent']}\n"
                "- Link to parent (for any issue type): {'parent': 'PROJ-123'}\n"
                "- Set Fix Version/s: {'fixVersions': [{'id': '10020'}]}\n"
                "- Custom fields: {'customfield_10010': 'value'}"
            ),
            default=None,
        ),
    ] = None,
) -> str:
    """Create a new Jira issue with optional Epic link or parent for subtasks.

    Args:
        ctx: The FastMCP context.
        project_key: The JIRA project key.
        summary: Summary/title of the issue.
        issue_type: Issue type (e.g., 'Task', 'Bug', 'Story', 'Epic', 'Subtask').
        assignee: Assignee's user identifier (string): Email, display name, or account ID (e.g., 'user@example.com', 'John Doe', 'accountid:...').
        description: Issue description.
        components: Comma-separated list of component names.
        additional_fields: Dictionary or JSON string of additional fields.

    Returns:
        JSON string representing the created issue object.

    Raises:
        ValueError: If in read-only mode or Jira client is unavailable.
    """
    jira = await get_jira_fetcher(ctx)
    # Parse components from comma-separated string to list
    components_list = None
    if components and isinstance(components, str):
        components_list = [
            comp.strip() for comp in components.split(",") if comp.strip()
        ]

    # Use additional_fields directly as dict
    # Accept either dict or JSON string for additional fields
    if additional_fields is None:
        extra_fields: dict[str, Any] = {}
    elif isinstance(additional_fields, dict):
        extra_fields = additional_fields
    elif isinstance(additional_fields, str):
        try:
            extra_fields = json.loads(additional_fields)
            if not isinstance(extra_fields, dict):
                raise ValueError(
                    "Parsed additional_fields is not a JSON object (dict)."
                )
        except json.JSONDecodeError as e:
            raise ValueError(f"additional_fields is not valid JSON: {e}") from e
    else:
        raise ValueError("additional_fields must be a dictionary or JSON string.")

    issue = jira.create_issue(
        project_key=project_key,
        summary=summary,
        issue_type=issue_type,
        description=description,
        assignee=assignee,
        components=components_list,
        **extra_fields,
    )
    result = issue.to_simplified_dict()
    return json.dumps(
        {"message": "Issue created successfully", "issue": result},
        indent=2,
        ensure_ascii=False,
    )


@jira_mcp.tool(
    tags={"jira", "write"},
    annotations={"title": "Batch Create Issues", "destructiveHint": True},
)
@check_write_access
async def batch_create_issues(
    ctx: Context,
    issues: Annotated[
        str,
        Field(
            description=(
                "JSON array of issue objects. Each object should contain:\n"
                "- project_key (required): The project key (e.g., 'PROJ')\n"
                "- summary (required): Issue summary/title\n"
                "- issue_type (required): Type of issue (e.g., 'Task', 'Bug')\n"
                "- description (optional): Issue description\n"
                "- assignee (optional): Assignee username or email\n"
                "- components (optional): Array of component names\n"
                "Example: [\n"
                '  {"project_key": "PROJ", "summary": "Issue 1", "issue_type": "Task"},\n'
                '  {"project_key": "PROJ", "summary": "Issue 2", "issue_type": "Bug", "components": ["Frontend"]}\n'
                "]"
            )
        ),
    ],
    validate_only: Annotated[
        bool,
        Field(
            description="If true, only validates the issues without creating them",
            default=False,
        ),
    ] = False,
) -> str:
    """Create multiple Jira issues in a batch.

    Args:
        ctx: The FastMCP context.
        issues: JSON array string of issue objects.
        validate_only: If true, only validates without creating.

    Returns:
        JSON string indicating success and listing created issues (or validation result).

    Raises:
        ValueError: If in read-only mode, Jira client unavailable, or invalid JSON.
    """
    jira = await get_jira_fetcher(ctx)
    # Parse issues from JSON string
    try:
        issues_list = json.loads(issues)
        if not isinstance(issues_list, list):
            raise ValueError("Input 'issues' must be a JSON array string.")
    except json.JSONDecodeError:
        raise ValueError("Invalid JSON in issues")
    except Exception as e:
        raise ValueError(f"Invalid input for issues: {e}") from e

    # Create issues in batch
    created_issues = jira.batch_create_issues(issues_list, validate_only=validate_only)

    message = (
        "Issues validated successfully"
        if validate_only
        else "Issues created successfully"
    )
    result = {
        "message": message,
        "issues": [issue.to_simplified_dict() for issue in created_issues],
    }
    return json.dumps(result, indent=2, ensure_ascii=False)


@jira_mcp.tool(
    tags={"jira", "read"},
    annotations={"title": "Batch Get Changelogs", "readOnlyHint": True},
)
async def batch_get_changelogs(
    ctx: Context,
    issue_ids_or_keys: Annotated[
        list[str],
        Field(
            description="List of Jira issue IDs or keys, e.g. ['PROJ-123', 'PROJ-124']"
        ),
    ],
    fields: Annotated[
        list[str] | None,
        Field(
            description="(Optional) Filter the changelogs by fields, e.g. ['status', 'assignee']. Default to None for all fields.",
            default=None,
        ),
    ] = None,
    limit: Annotated[
        int,
        Field(
            description=(
                "Maximum number of changelogs to return in result for each issue. "
                "Default to -1 for all changelogs. "
                "Notice that it only limits the results in the response, "
                "the function will still fetch all the data."
            ),
            default=-1,
        ),
    ] = -1,
) -> str:
    """Get changelogs for multiple Jira issues (Cloud only).

    Args:
        ctx: The FastMCP context.
        issue_ids_or_keys: List of issue IDs or keys.
        fields: List of fields to filter changelogs by. None for all fields.
        limit: Maximum changelogs per issue (-1 for all).

    Returns:
        JSON string representing a list of issues with their changelogs.

    Raises:
        NotImplementedError: If run on Jira Server/Data Center.
        ValueError: If Jira client is unavailable.
    """
    jira = await get_jira_fetcher(ctx)
    # Ensure this runs only on Cloud, as per original function docstring
    if not jira.config.is_cloud:
        raise NotImplementedError(
            "Batch get issue changelogs is only available on Jira Cloud."
        )

    # Call the underlying method
    issues_with_changelogs = jira.batch_get_changelogs(
        issue_ids_or_keys=issue_ids_or_keys, fields=fields
    )

    # Format the response
    results = []
    limit_val = None if limit == -1 else limit
    for issue in issues_with_changelogs:
        results.append(
            {
                "issue_id": issue.id,
                "changelogs": [
                    changelog.to_simplified_dict()
                    for changelog in issue.changelogs[:limit_val]
                ],
            }
        )
    return json.dumps(results, indent=2, ensure_ascii=False)


@jira_mcp.tool(
    tags={"jira", "write"},
    annotations={"title": "Update Issue", "destructiveHint": True},
)
@check_write_access
async def update_issue(
    ctx: Context,
    issue_key: Annotated[str, Field(description="Jira issue key (e.g., 'PROJ-123')")],
    fields: Annotated[
        dict[str, Any],
        Field(
            description=(
                "Dictionary of fields to update. Special field handling:\n"
                "- 'assignee': Provide a string identifier (email, name, or accountId)\n"
                "- 'components': Component operations:\n"
                "  * SET (replace all): [{'id': '10911'}, {'id': '10912'}]\n"
                "  * ADD/REMOVE: {'add': [{'id': '10911'}], 'remove': [{'id': '10912'}]}\n"
                "  * Use 'add_components' and 'remove_components' in additional_fields for convenience\n"
                "- 'description': Provide markdown text (will be converted to Jira format)\n"
                "- 'status': Provide status name to transition the issue\n"
                "Example: `{'assignee': 'user@example.com', 'components': [{'id': '10911'}]}`"
            )
        ),
    ],
    additional_fields: Annotated[
        dict[str, Any] | None,
        Field(
            description=(
                "(Optional) Dictionary of additional fields to update.\n"
                "Special keys:\n"
                "- 'add_components': [{'id': '10911'}] - Add components without replacing existing ones\n"
                "- 'remove_components': [{'id': '10912'}] - Remove specific components\n"
                "Use this for custom fields or component add/remove operations."
            ),
            default=None,
        ),
    ] = None,
    attachments: Annotated[
        str | None,
        Field(
            description=(
                "(Optional) JSON string array or comma-separated list of file paths to attach to the issue. "
                "Example: '/path/to/file1.txt,/path/to/file2.txt' or ['/path/to/file1.txt','/path/to/file2.txt']"
            ),
            default=None,
        ),
    ] = None,
) -> str:
    """Update an existing Jira issue including changing status, adding Epic links, updating fields, etc.

    Args:
        ctx: The FastMCP context.
        issue_key: Jira issue key.
        fields: Dictionary of fields to update.
        additional_fields: Optional dictionary of additional fields.
        attachments: Optional JSON array string or comma-separated list of file paths.

    Returns:
        JSON string representing the updated issue object and attachment results.

    Raises:
        ValueError: If in read-only mode or Jira client unavailable, or invalid input.
    """
    jira = await get_jira_fetcher(ctx)
    # Use fields directly as dict
    if not isinstance(fields, dict):
        raise ValueError("fields must be a dictionary.")
    update_fields = fields

    # Use additional_fields directly as dict
    extra_fields = additional_fields or {}
    if not isinstance(extra_fields, dict):
        raise ValueError("additional_fields must be a dictionary.")

    # Parse attachments
    attachment_paths = []
    if attachments:
        if isinstance(attachments, str):
            try:
                parsed = json.loads(attachments)
                if isinstance(parsed, list):
                    attachment_paths = [str(p) for p in parsed]
                else:
                    raise ValueError("attachments JSON string must be an array.")
            except json.JSONDecodeError:
                # Assume comma-separated if not valid JSON array
                attachment_paths = [
                    p.strip() for p in attachments.split(",") if p.strip()
                ]
        else:
            raise ValueError(
                "attachments must be a JSON array string or comma-separated string."
            )

    # Combine fields and additional_fields
    all_updates = {**update_fields, **extra_fields}
    if attachment_paths:
        all_updates["attachments"] = attachment_paths

    try:
        issue = jira.update_issue(issue_key=issue_key, **all_updates)
        result = issue.to_simplified_dict()
        if (
            hasattr(issue, "custom_fields")
            and "attachment_results" in issue.custom_fields
        ):
            result["attachment_results"] = issue.custom_fields["attachment_results"]
        return json.dumps(
            {"message": "Issue updated successfully", "issue": result},
            indent=2,
            ensure_ascii=False,
        )
    except Exception as e:
        logger.error(f"Error updating issue {issue_key}: {str(e)}", exc_info=True)
        raise ValueError(f"Failed to update issue {issue_key}: {str(e)}")


@jira_mcp.tool(
    tags={"jira", "write"},
    annotations={"title": "Delete Issue", "destructiveHint": True},
)
@check_write_access
async def delete_issue(
    ctx: Context,
    issue_key: Annotated[str, Field(description="Jira issue key (e.g. PROJ-123)")],
) -> str:
    """Delete an existing Jira issue.

    Args:
        ctx: The FastMCP context.
        issue_key: Jira issue key.

    Returns:
        JSON string indicating success.

    Raises:
        ValueError: If in read-only mode or Jira client unavailable.
    """
    jira = await get_jira_fetcher(ctx)
    deleted = jira.delete_issue(issue_key)
    result = {"message": f"Issue {issue_key} has been deleted successfully."}
    # The underlying method raises on failure, so if we reach here, it's success.
    return json.dumps(result, indent=2, ensure_ascii=False)


@jira_mcp.tool(
    tags={"jira", "write"},
    annotations={"title": "Add Comment", "destructiveHint": True},
)
@check_write_access
async def add_comment(
    ctx: Context,
    issue_key: Annotated[str, Field(description="Jira issue key (e.g., 'PROJ-123')")],
    comment: Annotated[str, Field(description="Comment text in Markdown format")],
    visibility: Annotated[
        dict[str, str] | None,
        Field(
            description="""(Optional) Comment visibility (e.g. {"type":"group","value":"jira-users"})"""
        ),
    ] = None,
) -> str:
    """Add a comment to a Jira issue.

    Args:
        ctx: The FastMCP context.
        issue_key: Jira issue key.
        comment: Comment text in Markdown.
        visibility: (Optional) Comment visibility (e.g. {"type":"group","value":"jira-users"}).

    Returns:
        JSON string representing the added comment object.

    Raises:
        ValueError: If in read-only mode or Jira client unavailable.
    """
    jira = await get_jira_fetcher(ctx)
    # add_comment returns dict
    result = jira.add_comment(issue_key, comment, visibility)
    return json.dumps(result, indent=2, ensure_ascii=False)


@jira_mcp.tool(
    tags={"jira", "write"},
    annotations={"title": "Edit Comment", "destructiveHint": True},
)
@check_write_access
async def edit_comment(
    ctx: Context,
    issue_key: Annotated[str, Field(description="Jira issue key (e.g., 'PROJ-123')")],
    comment_id: Annotated[str, Field(description="The ID of the comment to edit")],
    comment: Annotated[
        str, Field(description="Updated comment text in Markdown format")
    ],
    visibility: Annotated[
        dict[str, str] | None,
        Field(
            description="""(Optional) Comment visibility (e.g. {"type":"group","value":"jira-users"})"""
        ),
    ] = None,
) -> str:
    """Edit an existing comment on a Jira issue.

    Args:
        ctx: The FastMCP context.
        issue_key: Jira issue key.
        comment_id: The ID of the comment to edit.
        comment: Updated comment text in Markdown.
        visibility: (Optional) Comment visibility (e.g. {"type":"group","value":"jira-users"}).

    Returns:
        JSON string representing the updated comment object.

    Raises:
        ValueError: If in read-only mode or Jira client unavailable.
    """
    jira = await get_jira_fetcher(ctx)
    # edit_comment returns dict
    result = jira.edit_comment(issue_key, comment_id, comment, visibility)
    return json.dumps(result, indent=2, ensure_ascii=False)


@jira_mcp.tool(
    tags={"jira", "write"},
    annotations={"title": "Add Worklog", "destructiveHint": True},
)
@check_write_access
async def add_worklog(
    ctx: Context,
    issue_key: Annotated[str, Field(description="Jira issue key (e.g., 'PROJ-123')")],
    time_spent: Annotated[
        str,
        Field(
            description=(
                "Time spent in Jira format. Examples: "
                "'1h 30m' (1 hour and 30 minutes), '1d' (1 day), '30m' (30 minutes), '4h' (4 hours)"
            )
        ),
    ],
    comment: Annotated[
        str | None,
        Field(description="(Optional) Comment for the worklog in Markdown format"),
    ] = None,
    started: Annotated[
        str | None,
        Field(
            description=(
                "(Optional) Start time in ISO format. If not provided, the current time will be used. "
                "Example: '2023-08-01T12:00:00.000+0000'"
            )
        ),
    ] = None,
    # Add original_estimate and remaining_estimate as per original tool
    original_estimate: Annotated[
        str | None, Field(description="(Optional) New value for the original estimate")
    ] = None,
    remaining_estimate: Annotated[
        str | None, Field(description="(Optional) New value for the remaining estimate")
    ] = None,
) -> str:
    """Add a worklog entry to a Jira issue.

    Args:
        ctx: The FastMCP context.
        issue_key: Jira issue key.
        time_spent: Time spent in Jira format.
        comment: Optional comment in Markdown.
        started: Optional start time in ISO format.
        original_estimate: Optional new original estimate.
        remaining_estimate: Optional new remaining estimate.


    Returns:
        JSON string representing the added worklog object.

    Raises:
        ValueError: If in read-only mode or Jira client unavailable.
    """
    jira = await get_jira_fetcher(ctx)
    # add_worklog returns dict
    worklog_result = jira.add_worklog(
        issue_key=issue_key,
        time_spent=time_spent,
        comment=comment,
        started=started,
        original_estimate=original_estimate,
        remaining_estimate=remaining_estimate,
    )
    result = {"message": "Worklog added successfully", "worklog": worklog_result}
    return json.dumps(result, indent=2, ensure_ascii=False)


@jira_mcp.tool(
    tags={"jira", "write"},
    annotations={"title": "Link to Epic", "destructiveHint": True},
)
@check_write_access
async def link_to_epic(
    ctx: Context,
    issue_key: Annotated[
        str, Field(description="The key of the issue to link (e.g., 'PROJ-123')")
    ],
    epic_key: Annotated[
        str, Field(description="The key of the epic to link to (e.g., 'PROJ-456')")
    ],
) -> str:
    """Link an existing issue to an epic.

    Args:
        ctx: The FastMCP context.
        issue_key: The key of the issue to link.
        epic_key: The key of the epic to link to.

    Returns:
        JSON string representing the updated issue object.

    Raises:
        ValueError: If in read-only mode or Jira client unavailable.
    """
    jira = await get_jira_fetcher(ctx)
    issue = jira.link_issue_to_epic(issue_key, epic_key)
    result = {
        "message": f"Issue {issue_key} has been linked to epic {epic_key}.",
        "issue": issue.to_simplified_dict(),
    }
    return json.dumps(result, indent=2, ensure_ascii=False)


@jira_mcp.tool(
    tags={"jira", "write"},
    annotations={"title": "Create Issue Link", "destructiveHint": True},
)
@check_write_access
async def create_issue_link(
    ctx: Context,
    link_type: Annotated[
        str,
        Field(
            description="The type of link to create (e.g., 'Duplicate', 'Blocks', 'Relates to')"
        ),
    ],
    inward_issue_key: Annotated[
        str, Field(description="The key of the inward issue (e.g., 'PROJ-123')")
    ],
    outward_issue_key: Annotated[
        str, Field(description="The key of the outward issue (e.g., 'PROJ-456')")
    ],
    comment: Annotated[
        str | None, Field(description="(Optional) Comment to add to the link")
    ] = None,
    comment_visibility: Annotated[
        dict[str, str] | None,
        Field(
            description="(Optional) Visibility settings for the comment (e.g., {'type': 'group', 'value': 'jira-users'})",
            default=None,
        ),
    ] = None,
) -> str:
    """Create a link between two Jira issues.

    Args:
        ctx: The FastMCP context.
        link_type: The type of link (e.g., 'Blocks').
        inward_issue_key: The key of the source issue.
        outward_issue_key: The key of the target issue.
        comment: Optional comment text.
        comment_visibility: Optional dictionary for comment visibility.

    Returns:
        JSON string indicating success or failure.

    Raises:
        ValueError: If required fields are missing, invalid input, in read-only mode, or Jira client unavailable.
    """
    jira = await get_jira_fetcher(ctx)
    if not all([link_type, inward_issue_key, outward_issue_key]):
        raise ValueError(
            "link_type, inward_issue_key, and outward_issue_key are required."
        )

    link_data = {
        "type": {"name": link_type},
        "inwardIssue": {"key": inward_issue_key},
        "outwardIssue": {"key": outward_issue_key},
    }

    if comment:
        comment_obj = {"body": comment}
        if comment_visibility and isinstance(comment_visibility, dict):
            if "type" in comment_visibility and "value" in comment_visibility:
                comment_obj["visibility"] = comment_visibility
            else:
                logger.warning("Invalid comment_visibility dictionary structure.")
        link_data["comment"] = comment_obj

    result = jira.create_issue_link(link_data)
    return json.dumps(result, indent=2, ensure_ascii=False)


@jira_mcp.tool(
    tags={"jira", "write"},
    annotations={"title": "Create Remote Issue Link", "destructiveHint": True},
)
@check_write_access
async def create_remote_issue_link(
    ctx: Context,
    issue_key: Annotated[
        str,
        Field(description="The key of the issue to add the link to (e.g., 'PROJ-123')"),
    ],
    url: Annotated[
        str,
        Field(
            description="The URL to link to (e.g., 'https://example.com/page' or Confluence page URL)"
        ),
    ],
    title: Annotated[
        str,
        Field(
            description="The title/name of the link (e.g., 'Documentation Page', 'Confluence Page')"
        ),
    ],
    summary: Annotated[
        str | None, Field(description="(Optional) Description of the link")
    ] = None,
    relationship: Annotated[
        str | None,
        Field(
            description="(Optional) Relationship description (e.g., 'causes', 'relates to', 'documentation')"
        ),
    ] = None,
    icon_url: Annotated[
        str | None, Field(description="(Optional) URL to a 16x16 icon for the link")
    ] = None,
) -> str:
    """Create a remote issue link (web link or Confluence link) for a Jira issue.

    This tool allows you to add web links and Confluence links to Jira issues.
    The links will appear in the issue's "Links" section and can be clicked to navigate to external resources.

    Args:
        ctx: The FastMCP context.
        issue_key: The key of the issue to add the link to.
        url: The URL to link to (can be any web page or Confluence page).
        title: The title/name that will be displayed for the link.
        summary: Optional description of what the link is for.
        relationship: Optional relationship description.
        icon_url: Optional URL to a 16x16 icon for the link.

    Returns:
        JSON string indicating success or failure.

    Raises:
        ValueError: If required fields are missing, invalid input, in read-only mode, or Jira client unavailable.
    """
    jira = await get_jira_fetcher(ctx)
    if not issue_key:
        raise ValueError("issue_key is required.")
    if not url:
        raise ValueError("url is required.")
    if not title:
        raise ValueError("title is required.")

    # Build the remote link data structure
    link_object = {
        "url": url,
        "title": title,
    }

    if summary:
        link_object["summary"] = summary

    if icon_url:
        link_object["icon"] = {"url16x16": icon_url, "title": title}

    link_data = {"object": link_object}

    if relationship:
        link_data["relationship"] = relationship

    result = jira.create_remote_issue_link(issue_key, link_data)
    return json.dumps(result, indent=2, ensure_ascii=False)


@jira_mcp.tool(
    tags={"jira", "write"},
    annotations={"title": "Remove Issue Link", "destructiveHint": True},
)
@check_write_access
async def remove_issue_link(
    ctx: Context,
    link_id: Annotated[str, Field(description="The ID of the link to remove")],
) -> str:
    """Remove a link between two Jira issues.

    Args:
        ctx: The FastMCP context.
        link_id: The ID of the link to remove.

    Returns:
        JSON string indicating success.

    Raises:
        ValueError: If link_id is missing, in read-only mode, or Jira client unavailable.
    """
    jira = await get_jira_fetcher(ctx)
    if not link_id:
        raise ValueError("link_id is required")

    result = jira.remove_issue_link(link_id)  # Returns dict on success
    return json.dumps(result, indent=2, ensure_ascii=False)


@jira_mcp.tool(
    tags={"jira", "write"},
    annotations={"title": "Transition Issue", "destructiveHint": True},
)
@check_write_access
async def transition_issue(
    ctx: Context,
    issue_key: Annotated[str, Field(description="Jira issue key (e.g., 'PROJ-123')")],
    transition_id: Annotated[
        str,
        Field(
            description=(
                "ID of the transition to perform. Use the jira_get_transitions tool first "
                "to get the available transition IDs for the issue. Example values: '11', '21', '31'"
            )
        ),
    ],
    fields: Annotated[
        dict[str, Any] | None,
        Field(
            description=(
                "(Optional) Dictionary of fields to update during the transition. "
                "Some transitions require specific fields to be set (e.g., resolution). "
                "Example: {'resolution': {'name': 'Fixed'}}"
            ),
            default=None,
        ),
    ] = None,
    comment: Annotated[
        str | None,
        Field(
            description=(
                "(Optional) Comment to add during the transition. "
                "This will be visible in the issue history."
            ),
        ),
    ] = None,
) -> str:
    """Transition a Jira issue to a new status.

    Args:
        ctx: The FastMCP context.
        issue_key: Jira issue key.
        transition_id: ID of the transition.
        fields: Optional dictionary of fields to update during transition.
        comment: Optional comment for the transition.

    Returns:
        JSON string representing the updated issue object.

    Raises:
        ValueError: If required fields missing, invalid input, in read-only mode, or Jira client unavailable.
    """
    jira = await get_jira_fetcher(ctx)
    if not issue_key or not transition_id:
        raise ValueError("issue_key and transition_id are required.")

    # Use fields directly as dict
    update_fields = fields or {}
    if not isinstance(update_fields, dict):
        raise ValueError("fields must be a dictionary.")

    issue = jira.transition_issue(
        issue_key=issue_key,
        transition_id=transition_id,
        fields=update_fields,
        comment=comment,
    )

    result = {
        "message": f"Issue {issue_key} transitioned successfully",
        "issue": issue.to_simplified_dict() if issue else None,
    }
    return json.dumps(result, indent=2, ensure_ascii=False)


@jira_mcp.tool(
    tags={"jira", "write"},
    annotations={"title": "Create Sprint", "destructiveHint": True},
)
@check_write_access
async def create_sprint(
    ctx: Context,
    board_id: Annotated[str, Field(description="The id of board (e.g., '1000')")],
    sprint_name: Annotated[
        str, Field(description="Name of the sprint (e.g., 'Sprint 1')")
    ],
    start_date: Annotated[
        str, Field(description="Start time for sprint (ISO 8601 format)")
    ],
    end_date: Annotated[
        str, Field(description="End time for sprint (ISO 8601 format)")
    ],
    goal: Annotated[
        str | None, Field(description="(Optional) Goal of the sprint")
    ] = None,
) -> str:
    """Create Jira sprint for a board.

    Args:
        ctx: The FastMCP context.
        board_id: Board ID.
        sprint_name: Sprint name.
        start_date: Start date (ISO format).
        end_date: End date (ISO format).
        goal: Optional sprint goal.

    Returns:
        JSON string representing the created sprint object.

    Raises:
        ValueError: If in read-only mode or Jira client unavailable.
    """
    jira = await get_jira_fetcher(ctx)
    sprint = jira.create_sprint(
        board_id=board_id,
        sprint_name=sprint_name,
        start_date=start_date,
        end_date=end_date,
        goal=goal,
    )
    return json.dumps(sprint.to_simplified_dict(), indent=2, ensure_ascii=False)


@jira_mcp.tool(
    tags={"jira", "write"},
    annotations={"title": "Update Sprint", "destructiveHint": True},
)
@check_write_access
async def update_sprint(
    ctx: Context,
    sprint_id: Annotated[str, Field(description="The id of sprint (e.g., '10001')")],
    sprint_name: Annotated[
        str | None, Field(description="(Optional) New name for the sprint")
    ] = None,
    state: Annotated[
        str | None,
        Field(description="(Optional) New state for the sprint (future|active|closed)"),
    ] = None,
    start_date: Annotated[
        str | None, Field(description="(Optional) New start date for the sprint")
    ] = None,
    end_date: Annotated[
        str | None, Field(description="(Optional) New end date for the sprint")
    ] = None,
    goal: Annotated[
        str | None, Field(description="(Optional) New goal for the sprint")
    ] = None,
) -> str:
    """Update jira sprint.

    Args:
        ctx: The FastMCP context.
        sprint_id: The ID of the sprint.
        sprint_name: Optional new name.
        state: Optional new state (future|active|closed).
        start_date: Optional new start date.
        end_date: Optional new end date.
        goal: Optional new goal.

    Returns:
        JSON string representing the updated sprint object or an error message.

    Raises:
        ValueError: If in read-only mode or Jira client unavailable.
    """
    jira = await get_jira_fetcher(ctx)
    sprint = jira.update_sprint(
        sprint_id=sprint_id,
        sprint_name=sprint_name,
        state=state,
        start_date=start_date,
        end_date=end_date,
        goal=goal,
    )

    if sprint is None:
        error_payload = {
            "error": f"Failed to update sprint {sprint_id}. Check logs for details."
        }
        return json.dumps(error_payload, indent=2, ensure_ascii=False)
    else:
        return json.dumps(sprint.to_simplified_dict(), indent=2, ensure_ascii=False)


@jira_mcp.tool(
    tags={"jira", "read"},
    annotations={"title": "Get Project Versions", "readOnlyHint": True},
)
async def get_project_versions(
    ctx: Context,
    project_key: Annotated[str, Field(description="Jira project key (e.g., 'PROJ')")],
) -> str:
    """Get all fix versions for a specific Jira project."""
    jira = await get_jira_fetcher(ctx)
    versions = jira.get_project_versions(project_key)
    return json.dumps(versions, indent=2, ensure_ascii=False)


@jira_mcp.tool(
    tags={"jira", "read", "admin"},
    annotations={"title": "Get Project Issue Types", "readOnlyHint": True},
)
async def get_project_issue_types(
    ctx: Context,
    project_key: Annotated[str, Field(description="Jira project key.")],
) -> str:
    """Get the issue types configured for a Jira project."""
    jira = await get_jira_fetcher(ctx)
    return json.dumps(
        jira.get_project_issue_types(project_key), indent=2, ensure_ascii=False
    )


@jira_mcp.tool(
    tags={"jira", "read", "admin"},
    annotations={"title": "Get Project Statuses", "readOnlyHint": True},
)
async def get_project_statuses(
    ctx: Context,
    project_key: Annotated[str, Field(description="Jira project key.")],
) -> str:
    """Get statuses configured for each issue type in a Jira project."""
    jira = await get_jira_fetcher(ctx)
    return json.dumps(
        jira.get_project_statuses(project_key), indent=2, ensure_ascii=False
    )


@jira_mcp.tool(
    tags={"jira", "read", "admin"},
    annotations={"title": "Get Project Workflow Scheme", "readOnlyHint": True},
)
async def get_workflow_scheme_for_project(
    ctx: Context,
    project_key: Annotated[str, Field(description="Jira project key.")],
) -> str:
    """Get the workflow scheme associated with a Jira project."""
    jira = await get_jira_fetcher(ctx)
    return json.dumps(
        jira.get_workflow_scheme_for_project(project_key),
        indent=2,
        ensure_ascii=False,
    )


@jira_mcp.tool(
    tags={"jira", "read"},
    annotations={"title": "Get All Projects", "readOnlyHint": True},
)
async def get_all_projects(
    ctx: Context,
    include_archived: Annotated[
        bool,
        Field(
            description="Whether to include archived projects in the results",
            default=False,
        ),
    ] = False,
) -> str:
    """Get all Jira projects accessible to the current user.

    Args:
        ctx: The FastMCP context.
        include_archived: Whether to include archived projects.

    Returns:
        JSON string representing a list of project objects accessible to the user.
        Project keys are always returned in uppercase.
        If JIRA_PROJECTS_FILTER is configured, only returns projects matching those keys.

    Raises:
        ValueError: If the Jira client is not configured or available.
    """
    try:
        jira = await get_jira_fetcher(ctx)
        projects = jira.get_all_projects(include_archived=include_archived)
    except (MCPAtlassianAuthenticationError, HTTPError, OSError, ValueError) as e:
        error_message = ""
        log_level = logging.ERROR
        if isinstance(e, MCPAtlassianAuthenticationError):
            error_message = f"Authentication/Permission Error: {str(e)}"
        elif isinstance(e, OSError | HTTPError):
            error_message = f"Network or API Error: {str(e)}"
        elif isinstance(e, ValueError):
            error_message = f"Configuration Error: {str(e)}"

        error_result = {
            "success": False,
            "error": error_message,
        }
        logger.log(log_level, f"get_all_projects failed: {error_message}")
        return json.dumps(error_result, indent=2, ensure_ascii=False)

    # Ensure all project keys are uppercase
    for project in projects:
        if "key" in project:
            project["key"] = project["key"].upper()

    # Apply project filter if configured
    if jira.config.projects_filter:
        # Split projects filter by commas and handle possible whitespace
        allowed_project_keys = {
            p.strip().upper() for p in jira.config.projects_filter.split(",")
        }
        projects = [
            project
            for project in projects
            if project.get("key") in allowed_project_keys
        ]

    return json.dumps(projects, indent=2, ensure_ascii=False)


@jira_mcp.tool(
    tags={"jira", "write"},
    annotations={"title": "Create Version", "destructiveHint": True},
)
@check_write_access
async def create_version(
    ctx: Context,
    project_key: Annotated[str, Field(description="Jira project key (e.g., 'PROJ')")],
    name: Annotated[str, Field(description="Name of the version")],
    start_date: Annotated[
        str | None, Field(description="Start date (YYYY-MM-DD)", default=None)
    ] = None,
    release_date: Annotated[
        str | None, Field(description="Release date (YYYY-MM-DD)", default=None)
    ] = None,
    description: Annotated[
        str | None, Field(description="Description of the version", default=None)
    ] = None,
) -> str:
    """Create a new fix version in a Jira project.

    Args:
        ctx: The FastMCP context.
        project_key: The project key.
        name: Name of the version.
        start_date: Start date (optional).
        release_date: Release date (optional).
        description: Description (optional).

    Returns:
        JSON string of the created version object.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        version = jira.create_project_version(
            project_key=project_key,
            name=name,
            start_date=start_date,
            release_date=release_date,
            description=description,
        )
        return json.dumps(version, indent=2, ensure_ascii=False)
    except Exception as e:
        logger.error(
            f"Error creating version in project {project_key}: {str(e)}", exc_info=True
        )
        return json.dumps(
            {"success": False, "error": str(e)}, indent=2, ensure_ascii=False
        )


@jira_mcp.tool(
    name="batch_create_versions",
    tags={"jira", "write"},
    annotations={"title": "Batch Create Versions", "destructiveHint": True},
)
@check_write_access
async def batch_create_versions(
    ctx: Context,
    project_key: Annotated[str, Field(description="Jira project key (e.g., 'PROJ')")],
    versions: Annotated[
        str,
        Field(
            description=(
                "JSON array of version objects. Each object should contain:\n"
                "- name (required): Name of the version\n"
                "- startDate (optional): Start date (YYYY-MM-DD)\n"
                "- releaseDate (optional): Release date (YYYY-MM-DD)\n"
                "- description (optional): Description of the version\n"
                "Example: [\n"
                '  {"name": "v1.0", "startDate": "2025-01-01", "releaseDate": "2025-02-01", "description": "First release"},\n'
                '  {"name": "v2.0"}\n'
                "]"
            )
        ),
    ],
) -> str:
    """Batch create multiple versions in a Jira project.

    Args:
        ctx: The FastMCP context.
        project_key: The project key.
        versions: JSON array string of version objects.

    Returns:
        JSON array of results, each with success flag, version or error.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        version_list = json.loads(versions)
        if not isinstance(version_list, list):
            raise ValueError("Input 'versions' must be a JSON array string.")
    except json.JSONDecodeError:
        raise ValueError("Invalid JSON in versions")
    except Exception as e:
        raise ValueError(f"Invalid input for versions: {e}") from e

    results = []
    if not version_list:
        return json.dumps(results, indent=2, ensure_ascii=False)

    for idx, v in enumerate(version_list):
        # Defensive: ensure v is a dict and has a name
        if not isinstance(v, dict) or not v.get("name"):
            results.append(
                {
                    "success": False,
                    "error": f"Item {idx}: Each version must be an object with at least a 'name' field.",
                }
            )
            continue
        try:
            version = jira.create_project_version(
                project_key=project_key,
                name=v["name"],
                start_date=v.get("startDate"),
                release_date=v.get("releaseDate"),
                description=v.get("description"),
            )
            results.append({"success": True, "version": version})
        except Exception as e:
            logger.error(
                f"Error creating version in batch for project {project_key}: {str(e)}",
                exc_info=True,
            )
            results.append({"success": False, "error": str(e), "input": v})
    return json.dumps(results, indent=2, ensure_ascii=False)


# ---------------------------------------------------------------------------
# Organization user administration tools
# Require JIRA_ORG_ID and JIRA_ORG_KEY (or JIRA_ADMIN_TOKEN) env vars.
# ---------------------------------------------------------------------------


@jira_mcp.tool(
    tags={"jira", "read", "admin"},
    annotations={"title": "Get Org User Statistics", "readOnlyHint": True},
)
async def get_org_user_statistics(ctx: Context) -> str:
    """Return a breakdown of all Atlassian organization users by status.

    Uses the v2 stats endpoint for efficient server-side aggregation.
    Requires JIRA_ORG_ID and JIRA_ORG_KEY (or JIRA_ADMIN_TOKEN).

    Returns:
        JSON with total_users, active_members, by_status, and by_claim_status counts.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        stats = jira.get_org_user_statistics()
        return json.dumps(
            {"success": True, "statistics": stats}, indent=2, ensure_ascii=False
        )
    except ValueError as e:
        return json.dumps(
            {"success": False, "error": str(e)}, indent=2, ensure_ascii=False
        )
    except HTTPError as e:
        logger.error(f"HTTP error in get_org_user_statistics: {e}", exc_info=True)
        return json.dumps(
            {"success": False, "error": f"API error: {e}"}, indent=2, ensure_ascii=False
        )
    except Exception as e:
        logger.error(f"Unexpected error in get_org_user_statistics: {e}", exc_info=True)
        return json.dumps(
            {"success": False, "error": str(e)}, indent=2, ensure_ascii=False
        )


@jira_mcp.tool(
    tags={"jira", "read", "admin"},
    annotations={"title": "List Org Users", "readOnlyHint": True},
)
async def list_org_users(
    ctx: Context,
    status_filter: Annotated[
        str,
        Field(
            description=(
                "Filter by composite status (matches admin UI exactly). "
                "One of: 'active', 'suspended', 'not_invited', 'deactivated', 'for_deletion'. "
                "Leave empty to return all users."
            ),
            default="",
        ),
    ] = "",
    claim_status: Annotated[
        str,
        Field(
            description=(
                "Filter by claim status: 'managed' for org-owned accounts, "
                "'unmanaged' for external/guest accounts, or leave empty for all."
            ),
            default="",
        ),
    ] = "",
    account_ids: Annotated[
        str,
        Field(
            description="(Optional) Comma-separated list of up to 10 account IDs to fetch.",
            default="",
        ),
    ] = "",
    group_ids: Annotated[
        str,
        Field(
            description="(Optional) Comma-separated list of up to 10 group IDs to filter by.",
            default="",
        ),
    ] = "",
    role_ids: Annotated[
        str,
        Field(
            description=(
                "(Optional) Comma-separated list of role IDs to filter by "
                "(e.g. 'atlassian/admin', 'atlassian/org-admin')."
            ),
            default="",
        ),
    ] = "",
    email_domains: Annotated[
        str,
        Field(
            description="(Optional) Comma-separated list of email domains to filter by (e.g. 'example.com').",
            default="",
        ),
    ] = "",
    search_term: Annotated[
        str,
        Field(
            description="(Optional) Search term matched against nickname and email.",
            default="",
        ),
    ] = "",
    account_status: Annotated[
        str,
        Field(
            description=(
                "(Optional) Filter by raw account lifecycle status: "
                "'active', 'inactive', or 'closed'."
            ),
            default="",
        ),
    ] = "",
    membership_status: Annotated[
        str,
        Field(
            description=(
                "(Optional) Filter by directory membership status: "
                "'active', 'suspended', or 'no_membership'."
            ),
            default="",
        ),
    ] = "",
    mfa_enabled: Annotated[
        str,
        Field(
            description="(Optional) Filter by two-step verification: 'true' or 'false'. Leave empty for all.",
            default="",
        ),
    ] = "",
) -> str:
    """List users in the Atlassian organization with optional filtering (v2 API).

    Status counts match the Atlassian admin UI exactly. Does not include
    productLastAccess — use get_inactive_org_users for inactivity analysis.
    Requires JIRA_ORG_ID and JIRA_ORG_KEY (or JIRA_ADMIN_TOKEN).

    Returns:
        JSON with a list of users and a total count.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        mfa: bool | None = None
        if mfa_enabled.lower() == "true":
            mfa = True
        elif mfa_enabled.lower() == "false":
            mfa = False

        users = jira.fetch_org_users_v2(
            status_filter=status_filter or None,
            claim_status=claim_status or None,
            account_ids=[x.strip() for x in account_ids.split(",") if x.strip()]
            or None,
            group_ids=[x.strip() for x in group_ids.split(",") if x.strip()] or None,
            role_ids=[x.strip() for x in role_ids.split(",") if x.strip()] or None,
            email_domains=[x.strip() for x in email_domains.split(",") if x.strip()]
            or None,
            search_term=search_term or None,
            account_status=account_status or None,
            membership_status=membership_status or None,
            mfa_enabled=mfa,
        )
        return json.dumps(
            {"success": True, "total": len(users), "users": users},
            indent=2,
            ensure_ascii=False,
        )
    except ValueError as e:
        return json.dumps(
            {"success": False, "error": str(e)}, indent=2, ensure_ascii=False
        )
    except HTTPError as e:
        logger.error(f"HTTP error in list_org_users: {e}", exc_info=True)
        return json.dumps(
            {"success": False, "error": f"API error: {e}"}, indent=2, ensure_ascii=False
        )
    except Exception as e:
        logger.error(f"Unexpected error in list_org_users: {e}", exc_info=True)
        return json.dumps(
            {"success": False, "error": str(e)}, indent=2, ensure_ascii=False
        )


@jira_mcp.tool(
    tags={"jira", "read", "admin"},
    annotations={"title": "Count Org Users", "readOnlyHint": True},
)
async def count_org_users(
    ctx: Context,
    status_filter: Annotated[
        str,
        Field(
            description=(
                "Filter by composite status: 'active', 'suspended', 'not_invited', "
                "'deactivated', 'for_deletion'. Leave empty for all."
            ),
            default="",
        ),
    ] = "",
    claim_status: Annotated[
        str,
        Field(
            description="Filter by claim status: 'managed', 'unmanaged', or leave empty for all.",
            default="",
        ),
    ] = "",
    group_ids: Annotated[
        str,
        Field(
            description="(Optional) Comma-separated list of up to 10 group IDs to filter by.",
            default="",
        ),
    ] = "",
    role_ids: Annotated[
        str,
        Field(
            description="(Optional) Comma-separated role IDs to filter by.",
            default="",
        ),
    ] = "",
    email_domains: Annotated[
        str,
        Field(
            description="(Optional) Comma-separated email domains to filter by.",
            default="",
        ),
    ] = "",
    search_term: Annotated[
        str,
        Field(
            description="(Optional) Search term matched against nickname and email.",
            default="",
        ),
    ] = "",
    mfa_enabled: Annotated[
        str,
        Field(
            description="(Optional) Filter by two-step verification: 'true' or 'false'.",
            default="",
        ),
    ] = "",
) -> str:
    """Return the count of org users matching the given filters (v2 count endpoint).

    Much cheaper than list_org_users when you only need a number.
    Requires JIRA_ORG_ID and JIRA_ORG_KEY (or JIRA_ADMIN_TOKEN).

    Returns:
        JSON with the matching user count.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        mfa: bool | None = None
        if mfa_enabled.lower() == "true":
            mfa = True
        elif mfa_enabled.lower() == "false":
            mfa = False

        count = jira.count_org_users(
            status_filter=status_filter or None,
            claim_status=claim_status or None,
            group_ids=[x.strip() for x in group_ids.split(",") if x.strip()] or None,
            role_ids=[x.strip() for x in role_ids.split(",") if x.strip()] or None,
            email_domains=[x.strip() for x in email_domains.split(",") if x.strip()]
            or None,
            search_term=search_term or None,
            mfa_enabled=mfa,
        )
        return json.dumps(
            {"success": True, "count": count}, indent=2, ensure_ascii=False
        )
    except ValueError as e:
        return json.dumps(
            {"success": False, "error": str(e)}, indent=2, ensure_ascii=False
        )
    except HTTPError as e:
        logger.error(f"HTTP error in count_org_users: {e}", exc_info=True)
        return json.dumps(
            {"success": False, "error": f"API error: {e}"}, indent=2, ensure_ascii=False
        )
    except Exception as e:
        logger.error(f"Unexpected error in count_org_users: {e}", exc_info=True)
        return json.dumps(
            {"success": False, "error": str(e)}, indent=2, ensure_ascii=False
        )


@jira_mcp.tool(
    tags={"jira", "read", "admin"},
    annotations={"title": "Get Org User Details", "readOnlyHint": True},
)
async def get_org_user_details(
    ctx: Context,
    account_id: Annotated[
        str,
        Field(description="The Atlassian account ID of the user to fetch."),
    ],
) -> str:
    """Fetch detailed v2 information about a single org user by account ID.

    Returns all MultiDirectoryUser fields including accountType, platformRoles,
    managementSource, mfaEnabled, jobTitle, department, etc.
    Requires JIRA_ORG_ID and JIRA_ORG_KEY (or JIRA_ADMIN_TOKEN).

    Returns:
        JSON with the user's full v2 profile.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        user = jira.get_org_user_details(account_id)
        return json.dumps({"success": True, "user": user}, indent=2, ensure_ascii=False)
    except ValueError as e:
        return json.dumps(
            {"success": False, "error": str(e)}, indent=2, ensure_ascii=False
        )
    except HTTPError as e:
        logger.error(
            f"HTTP error fetching user details {account_id}: {e}", exc_info=True
        )
        return json.dumps(
            {"success": False, "error": f"API error: {e}"}, indent=2, ensure_ascii=False
        )
    except Exception as e:
        logger.error(
            f"Unexpected error fetching user details {account_id}: {e}", exc_info=True
        )
        return json.dumps(
            {"success": False, "error": str(e)}, indent=2, ensure_ascii=False
        )


@jira_mcp.tool(
    tags={"jira", "read", "admin"},
    annotations={"title": "Get Org User Role Assignments", "readOnlyHint": True},
)
async def get_org_user_role_assignments(
    ctx: Context,
    account_id: Annotated[
        str,
        Field(description="The Atlassian account ID of the user."),
    ],
    resource_ids: Annotated[
        str,
        Field(
            description=(
                "(Optional) Comma-separated Atlassian Resource Identifiers (ARIs) to filter by "
                "(e.g. 'ari:cloud:jira-core::site/1')."
            ),
            default="",
        ),
    ] = "",
    role_ids: Annotated[
        str,
        Field(
            description="(Optional) Comma-separated role IDs to filter by (e.g. 'atlassian/admin').",
            default="",
        ),
    ] = "",
) -> str:
    """List all platform role assignments for an org user (v2 API).

    Shows which roles the user has on which resources (Jira sites, Confluence, etc.).
    Requires JIRA_ORG_ID and JIRA_ORG_KEY (or JIRA_ADMIN_TOKEN).

    Returns:
        JSON with a list of role assignment objects.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        assignments = jira.get_org_user_role_assignments(
            account_id=account_id,
            resource_ids=[x.strip() for x in resource_ids.split(",") if x.strip()]
            or None,
            role_ids=[x.strip() for x in role_ids.split(",") if x.strip()] or None,
        )
        return json.dumps(
            {"success": True, "total": len(assignments), "assignments": assignments},
            indent=2,
            ensure_ascii=False,
        )
    except ValueError as e:
        return json.dumps(
            {"success": False, "error": str(e)}, indent=2, ensure_ascii=False
        )
    except HTTPError as e:
        logger.error(
            f"HTTP error fetching role assignments for {account_id}: {e}", exc_info=True
        )
        return json.dumps(
            {"success": False, "error": f"API error: {e}"}, indent=2, ensure_ascii=False
        )
    except Exception as e:
        logger.error(
            f"Unexpected error fetching role assignments for {account_id}: {e}",
            exc_info=True,
        )
        return json.dumps(
            {"success": False, "error": str(e)}, indent=2, ensure_ascii=False
        )


@jira_mcp.tool(
    tags={"jira", "read", "admin"},
    annotations={"title": "Get Inactive Org Users", "readOnlyHint": True},
)
async def get_inactive_org_users(
    ctx: Context,
    days: Annotated[
        int,
        Field(
            description="Minimum number of days since last login to consider a user inactive.",
            ge=1,
            default=30,
        ),
    ] = 30,
    include_no_activity: Annotated[
        bool,
        Field(
            description=(
                "If true, also include users who have never logged in (no activity data at all). "
                "Default is false."
            ),
            default=False,
        ),
    ] = False,
    exclude_account_ids: Annotated[
        str,
        Field(
            description=(
                "(Optional) Comma-separated list of account IDs to exclude from results "
                "(e.g. service accounts you want to protect from suspension)."
            ),
            default="",
        ),
    ] = "",
    exclude_group_ids: Annotated[
        str,
        Field(
            description=(
                "(Optional) Comma-separated list of group IDs whose members should be excluded "
                "from results (e.g. a 'suspension-protected' group)."
            ),
            default="",
        ),
    ] = "",
) -> str:
    """Find active Atlassian organization users who have not logged in for at least N days.

    Only checks human ('atlassian' account type) users. Useful for identifying accounts
    to review or suspend. Requires JIRA_ORG_ID and JIRA_ORG_KEY (or JIRA_ADMIN_TOKEN).

    Returns:
        JSON with matching users, each including _days_inactive, _last_access, _last_product.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        excluded_ids = [x.strip() for x in exclude_account_ids.split(",") if x.strip()]
        excluded_groups = [x.strip() for x in exclude_group_ids.split(",") if x.strip()]

        users = jira.get_inactive_users(
            days=days,
            account_type="atlassian",
            include_no_activity=include_no_activity,
            exclude_account_ids=excluded_ids or None,
            exclude_group_ids=excluded_groups or None,
        )

        summary = {
            "total_inactive": len(users),
            "threshold_days": days,
            "includes_no_activity": include_no_activity,
        }
        return json.dumps(
            {"success": True, "summary": summary, "users": users},
            indent=2,
            ensure_ascii=False,
        )
    except ValueError as e:
        return json.dumps(
            {"success": False, "error": str(e)}, indent=2, ensure_ascii=False
        )
    except HTTPError as e:
        logger.error(f"HTTP error in get_inactive_org_users: {e}", exc_info=True)
        return json.dumps(
            {"success": False, "error": f"API error: {e}"}, indent=2, ensure_ascii=False
        )
    except Exception as e:
        logger.error(f"Unexpected error in get_inactive_org_users: {e}", exc_info=True)
        return json.dumps(
            {"success": False, "error": str(e)}, indent=2, ensure_ascii=False
        )


@jira_mcp.tool(
    tags={"jira", "write", "admin"},
    annotations={"title": "Suspend Org User", "destructiveHint": True},
)
@check_write_access
async def suspend_org_user(
    ctx: Context,
    account_id: Annotated[
        str,
        Field(description="The Atlassian account ID of the user to suspend."),
    ],
) -> str:
    """Suspend an Atlassian organization user by account ID.

    This deactivates the user's access across all Atlassian products in the organization.
    Requires JIRA_ORG_ID and JIRA_ORG_KEY (or JIRA_ADMIN_TOKEN) and write access.

    Returns:
        JSON indicating success or failure.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        jira.suspend_org_user(account_id)
        return json.dumps(
            {"success": True, "account_id": account_id, "action": "suspended"},
            indent=2,
            ensure_ascii=False,
        )
    except ValueError as e:
        return json.dumps(
            {"success": False, "error": str(e)}, indent=2, ensure_ascii=False
        )
    except HTTPError as e:
        logger.error(f"HTTP error suspending user {account_id}: {e}", exc_info=True)
        return json.dumps(
            {"success": False, "error": f"API error: {e}"}, indent=2, ensure_ascii=False
        )
    except Exception as e:
        logger.error(
            f"Unexpected error suspending user {account_id}: {e}", exc_info=True
        )
        return json.dumps(
            {"success": False, "error": str(e)}, indent=2, ensure_ascii=False
        )


@jira_mcp.tool(
    tags={"jira", "write", "admin"},
    annotations={"title": "Reactivate Org User"},
)
@check_write_access
async def reactivate_org_user(
    ctx: Context,
    account_id: Annotated[
        str,
        Field(description="The Atlassian account ID of the user to reactivate."),
    ],
) -> str:
    """Reactivate a suspended Atlassian organization user by account ID.

    Restores the user's access across all Atlassian products in the organization.
    Requires JIRA_ORG_ID and JIRA_ORG_KEY (or JIRA_ADMIN_TOKEN) and write access.

    Returns:
        JSON indicating success or failure.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        jira.reactivate_org_user(account_id)
        return json.dumps(
            {"success": True, "account_id": account_id, "action": "reactivated"},
            indent=2,
            ensure_ascii=False,
        )
    except ValueError as e:
        return json.dumps(
            {"success": False, "error": str(e)}, indent=2, ensure_ascii=False
        )
    except HTTPError as e:
        logger.error(f"HTTP error reactivating user {account_id}: {e}", exc_info=True)
        return json.dumps(
            {"success": False, "error": f"API error: {e}"}, indent=2, ensure_ascii=False
        )
    except Exception as e:
        logger.error(
            f"Unexpected error reactivating user {account_id}: {e}", exc_info=True
        )
        return json.dumps(
            {"success": False, "error": str(e)}, indent=2, ensure_ascii=False
        )


@jira_mcp.tool(
    tags={"jira", "write", "admin"},
    annotations={"title": "Remove Org User From Directory", "destructiveHint": True},
)
@check_write_access
async def remove_org_user_from_directory(
    ctx: Context,
    account_id: Annotated[
        str,
        Field(description="The Atlassian account ID of the user to remove."),
    ],
) -> str:
    """Remove a user from the organization directory (v2 API). Non-reversible.

    The user loses all roles and group memberships immediately. Billing stops.
    You must re-invite them if you want to restore access.
    Prefer suspend_org_user for temporary deactivation.
    Requires JIRA_ORG_ID and JIRA_ORG_KEY (or JIRA_ADMIN_TOKEN) and write access.

    Returns:
        JSON indicating success or failure.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        jira.remove_org_user_from_directory(account_id)
        return json.dumps(
            {
                "success": True,
                "account_id": account_id,
                "action": "removed_from_directory",
            },
            indent=2,
            ensure_ascii=False,
        )
    except ValueError as e:
        return json.dumps(
            {"success": False, "error": str(e)}, indent=2, ensure_ascii=False
        )
    except HTTPError as e:
        logger.error(
            f"HTTP error removing user {account_id} from directory: {e}", exc_info=True
        )
        return json.dumps(
            {"success": False, "error": f"API error: {e}"}, indent=2, ensure_ascii=False
        )
    except Exception as e:
        logger.error(
            f"Unexpected error removing user {account_id} from directory: {e}",
            exc_info=True,
        )
        return json.dumps(
            {"success": False, "error": str(e)}, indent=2, ensure_ascii=False
        )


@jira_mcp.tool(
    tags={"jira", "read"},
    annotations={"title": "Get Security Level Members", "readOnlyHint": True},
)
async def get_security_level_members(
    ctx: Context,
    level_id: Annotated[
        str,
        Field(description="Numeric ID of the Jira security level (e.g. '10001')."),
    ],
) -> str:
    """
    Get groups, users, and roles that have access to a Jira issue security level.

    Args:
        ctx: The FastMCP context.
        level_id: Numeric security level ID.

    Returns:
        JSON with ``success``, ``level_id``, ``total``, and ``members`` list.
        Each member has ``type`` (e.g. 'group', 'user', 'projectRole') and ``parameter``
        (group name, account ID, or role ID).

    Raises:
        ValueError: If the Jira client is not configured or available.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        result = jira.get_security_level_members(level_id)
        return json.dumps(result, indent=2, ensure_ascii=False)
    except MCPAtlassianAuthenticationError as e:
        return json.dumps(
            {"success": False, "error": str(e)}, indent=2, ensure_ascii=False
        )
    except HTTPError as e:
        logger.error(
            f"HTTP error fetching security level members for level {level_id}: {e}",
            exc_info=True,
        )
        return json.dumps(
            {"success": False, "error": f"API error: {e}"}, indent=2, ensure_ascii=False
        )
    except Exception as e:
        logger.error(
            f"Unexpected error fetching security level members for level {level_id}: {e}",
            exc_info=True,
        )
        return json.dumps(
            {"success": False, "error": str(e)}, indent=2, ensure_ascii=False
        )


# ---------------------------------------------------------------------------
# Screen, field context and field configuration administration tools
# Require the "Administer Jira" global permission (scope manage:jira-configuration).
# ---------------------------------------------------------------------------


def _split_csv(value: str) -> list[str]:
    return [x.strip() for x in value.split(",") if x.strip()]


def _admin_error(tool: str, e: Exception) -> str:
    """Format an error from a screen/field admin tool as a JSON string."""
    if isinstance(e, ValueError | TypeError):
        message = str(e)
    elif isinstance(e, MCPAtlassianAuthenticationError):
        message = f"Authentication/Permission Error: {e}"
    elif isinstance(e, HTTPError):
        status = e.response.status_code if e.response is not None else "unknown"
        hint = (
            " (requires the Administer Jira global permission)"
            if status in (401, 403)
            else ""
        )
        message = f"API error {status}{hint}: {e}"
        logger.error(f"HTTP error in {tool}: {e}", exc_info=True)
    else:
        message = str(e)
        logger.error(f"Unexpected error in {tool}: {e}", exc_info=True)
    return json.dumps(
        {"success": False, "error": message}, indent=2, ensure_ascii=False
    )


def _admin_ok(**data: Any) -> str:
    return json.dumps({"success": True, **data}, indent=2, ensure_ascii=False)


@jira_mcp.tool(
    tags={"jira", "read", "admin"},
    annotations={"title": "List Screens", "readOnlyHint": True},
)
async def list_screens(
    ctx: Context,
    query: Annotated[
        str | None, Field(description="Optional screen name query.", default=None)
    ] = None,
    start_at: Annotated[int, Field(description="Starting index.", default=0, ge=0)] = 0,
    max_results: Annotated[
        int, Field(description="Maximum results.", default=100, ge=1, le=100)
    ] = 100,
) -> str:
    """Search Jira screens available to the administrator."""
    jira = await get_jira_fetcher(ctx)
    try:
        return _admin_ok(**jira.list_screens(query, start_at, max_results))
    except Exception as e:
        return _admin_error("list_screens", e)


@jira_mcp.tool(
    tags={"jira", "read", "admin"},
    annotations={"title": "Get Field Options", "readOnlyHint": True},
)
async def get_field_options(
    ctx: Context,
    field_id: Annotated[str, Field(description="Custom field ID.")],
    context_id: Annotated[str, Field(description="Custom field context ID.")],
    option_id: Annotated[
        str | None, Field(description="Optional option ID.", default=None)
    ] = None,
) -> str:
    """Read options belonging to a custom field context."""
    jira = await get_jira_fetcher(ctx)
    try:
        return _admin_ok(
            options=jira.get_field_options(field_id, context_id, option_id=option_id)
        )
    except Exception as e:
        return _admin_error("get_field_options", e)


@jira_mcp.tool(
    tags={"jira", "write", "admin"},
    annotations={"title": "Add Field Options", "destructiveHint": True},
)
@check_write_access
async def add_field_options(
    ctx: Context,
    field_id: Annotated[str, Field(description="Custom field ID.")],
    context_id: Annotated[str, Field(description="Custom field context ID.")],
    options: Annotated[
        list[dict[str, Any]],
        Field(description="Option objects, e.g. [{'value': 'Norway'}]."),
    ],
) -> str:
    """Add one or more options to a custom field context."""
    jira = await get_jira_fetcher(ctx)
    try:
        return _admin_ok(
            action="options_added",
            options=jira.add_field_options(field_id, context_id, options),
        )
    except Exception as e:
        return _admin_error("add_field_options", e)


@jira_mcp.tool(
    tags={"jira", "write", "admin"},
    annotations={"title": "Update Field Option", "destructiveHint": True},
)
@check_write_access
async def update_field_option(
    ctx: Context,
    field_id: Annotated[str, Field(description="Custom field ID.")],
    context_id: Annotated[str, Field(description="Custom field context ID.")],
    option_id: Annotated[str, Field(description="Option ID.")],
    value: Annotated[
        str | None, Field(description="New option value.", default=None)
    ] = None,
    disabled: Annotated[
        bool | None, Field(description="Whether the option is disabled.", default=None)
    ] = None,
) -> str:
    """Update a custom field option."""
    jira = await get_jira_fetcher(ctx)
    try:
        return _admin_ok(
            action="option_updated",
            option=jira.update_field_option(
                field_id, context_id, option_id, value=value, disabled=disabled
            ),
        )
    except Exception as e:
        return _admin_error("update_field_option", e)


@jira_mcp.tool(
    tags={"jira", "write", "admin"},
    annotations={"title": "Remove Field Option", "destructiveHint": True},
)
@check_write_access
async def remove_field_option(
    ctx: Context,
    field_id: Annotated[str, Field(description="Custom field ID.")],
    context_id: Annotated[str, Field(description="Custom field context ID.")],
    option_id: Annotated[str, Field(description="Option ID.")],
) -> str:
    """Delete a custom field option."""
    jira = await get_jira_fetcher(ctx)
    try:
        jira.remove_field_option(field_id, context_id, option_id)
        return _admin_ok(action="option_removed", option_id=option_id)
    except Exception as e:
        return _admin_error("remove_field_option", e)


@jira_mcp.tool(
    tags={"jira", "write", "admin"},
    annotations={"title": "Reorder Field Options", "destructiveHint": True},
)
@check_write_access
async def reorder_field_options(
    ctx: Context,
    field_id: Annotated[str, Field(description="Custom field ID.")],
    context_id: Annotated[str, Field(description="Custom field context ID.")],
    option_ids: Annotated[list[str], Field(description="Option IDs to move.")],
    position: Annotated[
        str, Field(description="First, Last, or an option ID.", default="First")
    ] = "First",
) -> str:
    """Move custom field options to a position in the list."""
    jira = await get_jira_fetcher(ctx)
    try:
        return _admin_ok(
            action="options_reordered",
            result=jira.reorder_field_options(
                field_id, context_id, option_ids, position=position
            ),
        )
    except Exception as e:
        return _admin_error("reorder_field_options", e)


@jira_mcp.tool(
    tags={"jira", "read", "admin"},
    annotations={"title": "Search Statuses", "readOnlyHint": True},
)
async def search_statuses(
    ctx: Context,
    query: Annotated[
        str | None, Field(description="Optional status name query.", default=None)
    ] = None,
    project_id: Annotated[
        str | None, Field(description="Optional project ID.", default=None)
    ] = None,
    issue_type_id: Annotated[
        str | None, Field(description="Optional issue type ID.", default=None)
    ] = None,
    start_at: Annotated[int, Field(description="Starting index.", default=0, ge=0)] = 0,
    max_results: Annotated[
        int, Field(description="Maximum results.", default=50, ge=1, le=50)
    ] = 50,
) -> str:
    """Search Jira statuses."""
    jira = await get_jira_fetcher(ctx)
    try:
        return _admin_ok(
            **jira.search_statuses(
                query, project_id, issue_type_id, start_at, max_results
            )
        )
    except Exception as e:
        return _admin_error("search_statuses", e)


@jira_mcp.tool(
    tags={"jira", "write", "admin"},
    annotations={"title": "Create Statuses", "destructiveHint": True},
)
@check_write_access
async def create_statuses(
    ctx: Context,
    statuses: Annotated[
        list[dict[str, Any]], Field(description="Status definitions to create.")
    ],
) -> str:
    """Create Jira statuses."""
    jira = await get_jira_fetcher(ctx)
    try:
        return _admin_ok(
            action="statuses_created", statuses=jira.create_statuses(statuses)
        )
    except Exception as e:
        return _admin_error("create_statuses", e)


@jira_mcp.tool(
    tags={"jira", "write", "admin"},
    annotations={"title": "Update Statuses", "destructiveHint": True},
)
@check_write_access
async def update_statuses(
    ctx: Context,
    statuses: Annotated[
        list[dict[str, Any]], Field(description="Status definitions to update.")
    ],
) -> str:
    """Update Jira statuses, including renaming a status such as Godkjent."""
    jira = await get_jira_fetcher(ctx)
    try:
        return _admin_ok(
            action="statuses_updated", statuses=jira.update_statuses(statuses)
        )
    except Exception as e:
        return _admin_error("update_statuses", e)


@jira_mcp.tool(
    tags={"jira", "read", "admin"},
    annotations={"title": "Get Status Usages", "readOnlyHint": True},
)
async def get_status_usages(
    ctx: Context,
    status_id: Annotated[str, Field(description="Status ID.")],
) -> str:
    """Find workflows that use a Jira status."""
    jira = await get_jira_fetcher(ctx)
    try:
        return _admin_ok(usages=jira.get_status_usages(status_id))
    except Exception as e:
        return _admin_error("get_status_usages", e)


@jira_mcp.tool(
    tags={"jira", "read", "admin"},
    annotations={"title": "Search Workflows", "readOnlyHint": True},
)
async def search_workflows(
    ctx: Context,
    query: Annotated[
        str | None, Field(description="Optional workflow name query.", default=None)
    ] = None,
    start_at: Annotated[int, Field(description="Starting index.", default=0, ge=0)] = 0,
    max_results: Annotated[
        int, Field(description="Maximum results.", default=50, ge=1, le=50)
    ] = 50,
) -> str:
    """Search Jira workflows."""
    jira = await get_jira_fetcher(ctx)
    try:
        return _admin_ok(**jira.search_workflows(query, start_at, max_results))
    except Exception as e:
        return _admin_error("search_workflows", e)


@jira_mcp.tool(
    tags={"jira", "write", "admin"},
    annotations={"title": "Update Workflows", "destructiveHint": True},
)
@check_write_access
async def update_workflows(
    ctx: Context,
    workflows: Annotated[
        list[dict[str, Any]], Field(description="Workflow update definitions.")
    ],
) -> str:
    """Update Jira workflow definitions."""
    jira = await get_jira_fetcher(ctx)
    try:
        return _admin_ok(
            action="workflows_updated", result=jira.update_workflows(workflows)
        )
    except Exception as e:
        return _admin_error("update_workflows", e)


@jira_mcp.tool(
    tags={"jira", "write", "admin"},
    annotations={"title": "Publish Workflow Scheme", "destructiveHint": True},
)
@check_write_access
async def publish_workflow_scheme(
    ctx: Context,
    workflow_scheme_id: Annotated[str, Field(description="Workflow scheme ID.")],
    status_mappings: Annotated[
        list[dict[str, Any]] | None,
        Field(
            description="Optional status mappings for the draft scheme.", default=None
        ),
    ] = None,
) -> str:
    """Publish a draft Jira workflow scheme."""
    jira = await get_jira_fetcher(ctx)
    try:
        return _admin_ok(
            action="workflow_scheme_published",
            result=jira.publish_workflow_scheme(workflow_scheme_id, status_mappings),
        )
    except Exception as e:
        return _admin_error("publish_workflow_scheme", e)


@jira_mcp.tool(
    tags={"jira", "read", "admin"},
    annotations={"title": "Get Project Screen Schemes", "readOnlyHint": True},
)
async def get_project_screen_schemes(
    ctx: Context,
    projects: Annotated[
        str,
        Field(
            description=(
                "Comma-separated project keys or numeric IDs (e.g. 'BB5' or "
                "'BB5,BB6,PROJ'). Pass several projects to see which of them "
                "share the same screens."
            )
        ),
    ],
    include_screens: Annotated[
        bool,
        Field(
            description=(
                "If true, also resolve each screen scheme to its default/create/"
                "edit/view screen IDs and return 'screen_usage': for every screen, "
                "which of the given projects use it. Use before add_field_to_screen_tab "
                "to see the blast radius of a change."
            ),
            default=False,
        ),
    ] = False,
) -> str:
    """Find the issue type screen scheme, issue type mappings and (optionally) screens
    used by one or more classic Jira projects.

    Jira has no 'where is this screen used' endpoint, so shared screens are detected
    only among the projects you pass in. Team-managed projects have no issue type
    screen scheme and return null.

    Returns:
        JSON with 'projects' (issue_type_screen_scheme and issue_type_mappings per
        project) and, with include_screens, 'screen_schemes' and 'screen_usage'.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        result = jira.get_project_screen_schemes(
            _split_csv(projects), include_screens=include_screens
        )
        return _admin_ok(**result)
    except Exception as e:
        return _admin_error("get_project_screen_schemes", e)


@jira_mcp.tool(
    tags={"jira", "read", "admin"},
    annotations={"title": "Get Screen Scheme", "readOnlyHint": True},
)
async def get_screen_scheme(
    ctx: Context,
    screen_scheme_ids: Annotated[
        str,
        Field(description="Comma-separated screen scheme IDs (e.g. '10100,10101')."),
    ],
) -> str:
    """Get screen schemes: which screen is used for default/create/edit/view.

    Returns:
        JSON with 'screen_schemes', each having id, name, description and 'screens'
        (default/create/edit/view screen IDs).
    """
    jira = await get_jira_fetcher(ctx)
    try:
        schemes = jira.get_screen_schemes(_split_csv(screen_scheme_ids))
        return _admin_ok(screen_schemes=schemes)
    except Exception as e:
        return _admin_error("get_screen_scheme", e)


@jira_mcp.tool(
    tags={"jira", "read", "admin"},
    annotations={"title": "Get Screen Tabs", "readOnlyHint": True},
)
async def get_screen_tabs(
    ctx: Context,
    screen_id: Annotated[str, Field(description="Screen ID (e.g. '10200').")],
    project_key: Annotated[
        str | None,
        Field(description="(Optional) Project key to resolve tabs for.", default=None),
    ] = None,
) -> str:
    """Get the tabs of a screen.

    Returns:
        JSON with 'tabs' (id and name).
    """
    jira = await get_jira_fetcher(ctx)
    try:
        return _admin_ok(tabs=jira.get_screen_tabs(screen_id, project_key))
    except Exception as e:
        return _admin_error("get_screen_tabs", e)


@jira_mcp.tool(
    tags={"jira", "read", "admin"},
    annotations={"title": "Get Screen Tab Fields", "readOnlyHint": True},
)
async def get_screen_tab_fields(
    ctx: Context,
    screen_id: Annotated[str, Field(description="Screen ID (e.g. '10200').")],
    tab_id: Annotated[
        str, Field(description="Tab ID (from get_screen_tabs, e.g. '10300').")
    ],
) -> str:
    """Get the fields on a screen tab.

    Returns:
        JSON with 'fields' (id and name).
    """
    jira = await get_jira_fetcher(ctx)
    try:
        return _admin_ok(fields=jira.get_screen_tab_fields(screen_id, tab_id))
    except Exception as e:
        return _admin_error("get_screen_tab_fields", e)


@jira_mcp.tool(
    tags={"jira", "read", "admin"},
    annotations={"title": "Get Screen Available Fields", "readOnlyHint": True},
)
async def get_screen_available_fields(
    ctx: Context,
    screen_id: Annotated[str, Field(description="Screen ID (e.g. '10200').")],
) -> str:
    """Get the fields that can still be added to a screen.

    Returns:
        JSON with 'fields' (id and name).
    """
    jira = await get_jira_fetcher(ctx)
    try:
        return _admin_ok(fields=jira.get_screen_available_fields(screen_id))
    except Exception as e:
        return _admin_error("get_screen_available_fields", e)


@jira_mcp.tool(
    tags={"jira", "write", "admin"},
    annotations={"title": "Add Field To Screen Tab", "destructiveHint": True},
)
@check_write_access
async def add_field_to_screen_tab(
    ctx: Context,
    screen_id: Annotated[str, Field(description="Screen ID (e.g. '10200').")],
    tab_id: Annotated[str, Field(description="Tab ID (e.g. '10300').")],
    field_id: Annotated[
        str, Field(description="Field ID to add (e.g. 'customfield_10914').")
    ],
) -> str:
    """Add a field to a screen tab.

    This affects EVERY project that shares the screen. Check
    get_project_screen_schemes(include_screens=true) first. The field also needs a
    matching field context before it shows up. Undo with remove_field_from_screen_tab.

    Returns:
        JSON with the added field (id and name).
    """
    jira = await get_jira_fetcher(ctx)
    try:
        field = jira.add_field_to_screen_tab(screen_id, tab_id, field_id)
        return _admin_ok(
            action="field_added", screen_id=screen_id, tab_id=tab_id, field=field
        )
    except Exception as e:
        return _admin_error("add_field_to_screen_tab", e)


@jira_mcp.tool(
    tags={"jira", "write", "admin"},
    annotations={"title": "Remove Field From Screen Tab", "destructiveHint": True},
)
@check_write_access
async def remove_field_from_screen_tab(
    ctx: Context,
    screen_id: Annotated[str, Field(description="Screen ID (e.g. '10200').")],
    tab_id: Annotated[str, Field(description="Tab ID (e.g. '10300').")],
    field_id: Annotated[
        str, Field(description="Field ID to remove (e.g. 'customfield_10914').")
    ],
) -> str:
    """Remove a field from a screen tab (rollback for add_field_to_screen_tab).

    This affects EVERY project that shares the screen.

    Returns:
        JSON indicating success or failure.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        jira.remove_field_from_screen_tab(screen_id, tab_id, field_id)
        return _admin_ok(
            action="field_removed",
            screen_id=screen_id,
            tab_id=tab_id,
            field_id=field_id,
        )
    except Exception as e:
        return _admin_error("remove_field_from_screen_tab", e)


@jira_mcp.tool(
    tags={"jira", "read", "admin"},
    annotations={"title": "Get Field Contexts", "readOnlyHint": True},
)
async def get_field_contexts(
    ctx: Context,
    field_id: Annotated[
        str, Field(description="Custom field ID (e.g. 'customfield_10914').")
    ],
) -> str:
    """Get all contexts of a custom field.

    A field only shows up in a project if one of its contexts covers that project
    (a global context covers all). Use get_field_context_project_mapping to see
    which projects each context covers.

    Returns:
        JSON with 'contexts' (id, name, description, isGlobalContext, isAnyIssueType).
    """
    jira = await get_jira_fetcher(ctx)
    try:
        return _admin_ok(contexts=jira.get_field_contexts(field_id))
    except Exception as e:
        return _admin_error("get_field_contexts", e)


@jira_mcp.tool(
    tags={"jira", "read", "admin"},
    annotations={"title": "Get Field Context Project Mapping", "readOnlyHint": True},
)
async def get_field_context_project_mapping(
    ctx: Context,
    field_id: Annotated[
        str, Field(description="Custom field ID (e.g. 'customfield_10914').")
    ],
    context_ids: Annotated[
        str,
        Field(
            description="(Optional) Comma-separated context IDs. Omit for all contexts.",
            default="",
        ),
    ] = "",
) -> str:
    """Get which projects each context of a custom field applies to.

    Returns:
        JSON with 'mappings' (contextId, projectId, isGlobalContext). A global
        context has no projectId.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        mappings = jira.get_field_context_project_mapping(
            field_id, _split_csv(context_ids) or None
        )
        return _admin_ok(mappings=mappings)
    except Exception as e:
        return _admin_error("get_field_context_project_mapping", e)


@jira_mcp.tool(
    tags={"jira", "write", "admin"},
    annotations={"title": "Create Field Context", "destructiveHint": True},
)
@check_write_access
async def create_field_context(
    ctx: Context,
    field_id: Annotated[
        str, Field(description="Custom field ID (e.g. 'customfield_10914').")
    ],
    name: Annotated[str, Field(description="Name of the context.")],
    description: Annotated[
        str | None,
        Field(description="(Optional) Description of the context.", default=None),
    ] = None,
    project_ids: Annotated[
        str,
        Field(
            description=(
                "(Optional) Comma-separated NUMERIC project IDs. Empty creates a "
                "GLOBAL context that applies to all projects."
            ),
            default="",
        ),
    ] = "",
    issue_type_ids: Annotated[
        str,
        Field(
            description=(
                "(Optional) Comma-separated issue type IDs. Empty applies to all "
                "issue types."
            ),
            default="",
        ),
    ] = "",
) -> str:
    """Create a custom field context.

    Leaving project_ids empty creates a global context, so pass project IDs unless
    that is intended.

    Returns:
        JSON with the created context, including its id.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        context = jira.create_field_context(
            field_id,
            name,
            description=description,
            project_ids=_split_csv(project_ids) or None,
            issue_type_ids=_split_csv(issue_type_ids) or None,
        )
        return _admin_ok(action="context_created", context=context)
    except Exception as e:
        return _admin_error("create_field_context", e)


@jira_mcp.tool(
    tags={"jira", "write", "admin"},
    annotations={"title": "Assign Context To Project", "destructiveHint": True},
)
@check_write_access
async def assign_context_to_project(
    ctx: Context,
    field_id: Annotated[
        str, Field(description="Custom field ID (e.g. 'customfield_10914').")
    ],
    context_id: Annotated[str, Field(description="Context ID.")],
    project_ids: Annotated[
        str,
        Field(description="Comma-separated NUMERIC project IDs to assign."),
    ],
) -> str:
    """Assign a custom field context to projects.

    Jira rejects this if any of the projects already has a context for the same
    field.

    Returns:
        JSON indicating success or failure.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        ids = _split_csv(project_ids)
        jira.assign_context_to_project(field_id, context_id, ids)
        return _admin_ok(
            action="context_assigned",
            field_id=field_id,
            context_id=context_id,
            project_ids=ids,
        )
    except Exception as e:
        return _admin_error("assign_context_to_project", e)


@jira_mcp.tool(
    tags={"jira", "write", "admin"},
    annotations={"title": "Create Custom Field", "destructiveHint": True},
)
@check_write_access
async def create_custom_field(
    ctx: Context,
    name: Annotated[str, Field(description="Display name of the field.")],
    field_type: Annotated[
        str,
        Field(
            description=(
                "Full field type key, e.g. "
                "'com.atlassian.jira.plugin.system.customfieldtypes:url', "
                "'com.atlassian.jira.plugin.system.customfieldtypes:textfield' or "
                "'com.atlassian.jira.plugin.system.customfieldtypes:textarea'."
            )
        ),
    ],
    description: Annotated[
        str | None,
        Field(description="(Optional) Description of the field.", default=None),
    ] = None,
    searcher_key: Annotated[
        str | None,
        Field(description="(Optional) Searcher key for the field.", default=None),
    ] = None,
) -> str:
    """Create a custom field.

    The new field has no context and is on no screen until you add them.

    Returns:
        JSON with the created field definition, including its id.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        field = jira.create_custom_field(
            name, field_type, description=description, searcher_key=searcher_key
        )
        return _admin_ok(action="field_created", field=field)
    except Exception as e:
        return _admin_error("create_custom_field", e)


@jira_mcp.tool(
    tags={"jira", "write", "admin"},
    annotations={"title": "Create Field", "destructiveHint": True},
)
@check_write_access
async def create_field(
    ctx: Context,
    name: Annotated[str, Field(description="Display name of the field.")],
    field_type: Annotated[str, Field(description="Full Jira custom field type key.")],
    description: Annotated[
        str | None, Field(description="Optional field description.", default=None)
    ] = None,
    searcher_key: Annotated[
        str | None, Field(description="Optional searcher key.", default=None)
    ] = None,
) -> str:
    """Compatibility alias for creating a custom field."""
    jira = await get_jira_fetcher(ctx)
    try:
        field = jira.create_custom_field(
            name, field_type, description=description, searcher_key=searcher_key
        )
        return _admin_ok(action="field_created", field=field)
    except Exception as e:
        return _admin_error("create_field", e)


@jira_mcp.tool(
    tags={"jira", "write", "admin"},
    annotations={"title": "Update Field", "destructiveHint": True},
)
@check_write_access
async def update_field(
    ctx: Context,
    field_id: Annotated[
        str, Field(description="Custom field ID (e.g. 'customfield_10914').")
    ],
    name: Annotated[
        str | None, Field(description="(Optional) New field name.", default=None)
    ] = None,
    description: Annotated[
        str | None,
        Field(description="(Optional) New field description.", default=None),
    ] = None,
) -> str:
    """Update the name and/or description of a custom field.

    Returns:
        JSON indicating success or failure.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        jira.update_field(field_id, name=name, description=description)
        return _admin_ok(action="field_updated", field_id=field_id)
    except Exception as e:
        return _admin_error("update_field", e)


@jira_mcp.tool(
    tags={"jira", "read", "admin"},
    annotations={
        "title": "Get Project Field Configuration Scheme",
        "readOnlyHint": True,
    },
)
async def get_project_field_configuration_scheme(
    ctx: Context,
    projects: Annotated[
        str, Field(description="Comma-separated project keys or numeric IDs.")
    ],
) -> str:
    """Get the field configuration scheme used by projects.

    A project without an entry that has a 'fieldConfigurationScheme' uses the
    default field configuration.

    Returns:
        JSON with 'field_configuration_schemes' (fieldConfigurationScheme, projectIds).
    """
    jira = await get_jira_fetcher(ctx)
    try:
        schemes = jira.get_project_field_configuration_scheme(_split_csv(projects))
        return _admin_ok(field_configuration_schemes=schemes)
    except Exception as e:
        return _admin_error("get_project_field_configuration_scheme", e)


@jira_mcp.tool(
    tags={"jira", "read", "admin"},
    annotations={"title": "Get Field Configuration Items", "readOnlyHint": True},
)
async def get_field_configuration_items(
    ctx: Context,
    field_configuration_id: Annotated[
        str, Field(description="Field configuration ID.")
    ],
) -> str:
    """Get the fields of a field configuration with their hidden/required state.

    Returns:
        JSON with 'items' (id, isHidden, isRequired, description, renderer).
    """
    jira = await get_jira_fetcher(ctx)
    try:
        items = jira.get_field_configuration_items(field_configuration_id)
        return _admin_ok(items=items)
    except Exception as e:
        return _admin_error("get_field_configuration_items", e)


# ---------------------------------------------------------------------------
# Screen creation tools. Require the "Administer Jira" global permission.
# ---------------------------------------------------------------------------


@jira_mcp.tool(
    tags={"jira", "write", "admin"},
    annotations={"title": "Create Screen", "destructiveHint": True},
)
@check_write_access
async def create_screen(
    ctx: Context,
    name: Annotated[str, Field(description="Screen name; must be unique.")],
    description: Annotated[
        str | None, Field(description="(Optional) Screen description.", default=None)
    ] = None,
) -> str:
    """Create a screen.

    The screen is not used by any project until a screen scheme references it.

    Returns:
        JSON with the created screen, including its id.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        screen = jira.create_screen(name, description=description)
        return _admin_ok(action="screen_created", screen=screen)
    except Exception as e:
        return _admin_error("create_screen", e)


@jira_mcp.tool(
    tags={"jira", "write", "admin"},
    annotations={"title": "Add Screen Tab", "destructiveHint": True},
)
@check_write_access
async def add_screen_tab(
    ctx: Context,
    screen_id: Annotated[str, Field(description="Screen ID.")],
    tab_name: Annotated[str, Field(description="Name of the new tab.")],
) -> str:
    """Add a tab to a screen.

    Returns:
        JSON with the created tab, including its id.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        tab = jira.add_screen_tab(screen_id, tab_name)
        return _admin_ok(action="tab_added", tab=tab)
    except Exception as e:
        return _admin_error("add_screen_tab", e)


@jira_mcp.tool(
    tags={"jira", "write", "admin"},
    annotations={"title": "Copy Screen", "destructiveHint": True},
)
@check_write_access
async def copy_screen(
    ctx: Context,
    source_screen_id: Annotated[str, Field(description="ID of the screen to copy.")],
    new_name: Annotated[
        str, Field(description="Name for the new screen; must be unique.")
    ],
    description: Annotated[
        str | None,
        Field(
            description=(
                "(Optional) Description for the new screen; defaults to the "
                "source screen's."
            ),
            default=None,
        ),
    ] = None,
) -> str:
    """Copy a screen with its tabs and fields, preserving field order.

    There is no REST endpoint for copying, so the screen is recreated tab by
    tab and field by field.

    Returns:
        JSON with the created screen and a 'copy_summary'.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        screen = jira.copy_screen(source_screen_id, new_name, description=description)
        return _admin_ok(action="screen_copied", screen=screen)
    except Exception as e:
        return _admin_error("copy_screen", e)


@jira_mcp.tool(
    tags={"jira", "write", "admin"},
    annotations={"title": "Update Screen Scheme", "destructiveHint": True},
)
@check_write_access
async def update_screen_scheme(
    ctx: Context,
    screen_scheme_id: Annotated[str, Field(description="Screen scheme ID.")],
    name: Annotated[
        str | None, Field(description="(Optional) New scheme name.", default=None)
    ] = None,
    description: Annotated[
        str | None,
        Field(description="(Optional) New scheme description.", default=None),
    ] = None,
    default_screen_id: Annotated[
        str | None, Field(description="(Optional) Default screen ID.", default=None)
    ] = None,
    create_screen_id: Annotated[
        str | None, Field(description="(Optional) Create screen ID.", default=None)
    ] = None,
    edit_screen_id: Annotated[
        str | None, Field(description="(Optional) Edit screen ID.", default=None)
    ] = None,
    view_screen_id: Annotated[
        str | None, Field(description="(Optional) View screen ID.", default=None)
    ] = None,
) -> str:
    """Update a screen scheme (name, description and/or assigned screens).

    Only the given values are changed; this affects every project that uses
    the scheme.

    Returns:
        JSON indicating success or failure.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        jira.update_screen_scheme(
            screen_scheme_id,
            name=name,
            description=description,
            default_screen_id=default_screen_id,
            create_screen_id=create_screen_id,
            edit_screen_id=edit_screen_id,
            view_screen_id=view_screen_id,
        )
        return _admin_ok(
            action="screen_scheme_updated", screen_scheme_id=screen_scheme_id
        )
    except Exception as e:
        return _admin_error("update_screen_scheme", e)


# ---------------------------------------------------------------------------
# Automation rule tools. Use the Automation REST API on api.atlassian.com,
# which Atlassian only allows with an API token (not OAuth).
# ---------------------------------------------------------------------------


@jira_mcp.tool(
    tags={"jira", "read", "admin"},
    annotations={"title": "List Automation Rules", "readOnlyHint": True},
)
async def list_automation_rules(
    ctx: Context,
    limit: Annotated[
        int, Field(description="Page size.", default=50, ge=1, le=100)
    ] = 50,
    cursor: Annotated[
        str | None,
        Field(
            description=("(Optional) Opaque cursor from a previous response's links."),
            default=None,
        ),
    ] = None,
    trigger: Annotated[
        str | None,
        Field(
            description=(
                "(Optional) Filter by trigger, e.g. "
                "'jira.version.event.trigger:created'."
            ),
            default=None,
        ),
    ] = None,
    state: Annotated[
        str | None,
        Field(
            description="(Optional) Filter by state: ENABLED or DISABLED.",
            default=None,
        ),
    ] = None,
    scope: Annotated[
        str | None,
        Field(
            description=(
                "(Optional) Filter by a single ARI, e.g. a project scope "
                "'ari:cloud:jira:<cloud-id>:project/<project-id>'."
            ),
            default=None,
        ),
    ] = None,
) -> str:
    """List automation rule summaries (name, state, scope, labels).

    Requires API token authentication; Atlassian blocks OAuth apps from this
    API.

    Returns:
        JSON with 'rules' and 'links' (next cursor when more rules exist).
    """
    jira = await get_jira_fetcher(ctx)
    try:
        summary = jira.list_automation_rules(
            limit=limit,
            cursor=cursor,
            trigger=trigger,
            state=state,
            scope=scope,
        )
        return _admin_ok(rules=summary.get("data", []), links=summary.get("links"))
    except Exception as e:
        return _admin_error("list_automation_rules", e)


@jira_mcp.tool(
    tags={"jira", "read", "admin"},
    annotations={"title": "Get Automation Rule", "readOnlyHint": True},
)
async def get_automation_rule(
    ctx: Context,
    rule_uuid: Annotated[str, Field(description="UUID of the rule.")],
) -> str:
    """Get a full automation rule payload by UUID.

    Returns:
        JSON with the rule config, including trigger and components.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        rule = jira.get_automation_rule(rule_uuid)
        return _admin_ok(rule=rule)
    except Exception as e:
        return _admin_error("get_automation_rule", e)


@jira_mcp.tool(
    tags={"jira", "write", "admin"},
    annotations={"title": "Create Automation Rule", "destructiveHint": True},
)
@check_write_access
async def create_automation_rule(
    ctx: Context,
    rule: Annotated[
        dict[str, Any],
        Field(
            description=(
                "Rule payload: name, state, trigger, components, "
                "ruleScopeARIs, ... Use get_automation_rule on an existing "
                "rule to see the exact structure."
            )
        ),
    ],
    connections: Annotated[
        list[dict[str, Any]] | None,
        Field(
            description=(
                "(Optional) Connection definitions the rule needs (only for "
                "component types that use connections)."
            ),
            default=None,
        ),
    ] = None,
) -> str:
    """Create an automation rule.

    Requires API token authentication; Atlassian blocks OAuth apps from this
    API.

    Returns:
        JSON with the created rule's 'ruleUuid'.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        result = jira.create_automation_rule(rule, connections=connections)
        return _admin_ok(action="rule_created", **result)
    except Exception as e:
        return _admin_error("create_automation_rule", e)


@jira_mcp.tool(
    tags={"jira", "write", "admin"},
    annotations={"title": "Update Automation Rule", "destructiveHint": True},
)
@check_write_access
async def update_automation_rule(
    ctx: Context,
    rule_uuid: Annotated[str, Field(description="UUID of the rule to update.")],
    rule: Annotated[
        dict[str, Any],
        Field(
            description=(
                "The complete rule payload, same structure as "
                "get_automation_rule's response."
            )
        ),
    ],
    connections: Annotated[
        list[dict[str, Any]] | None,
        Field(description="(Optional) Connection definitions.", default=None),
    ] = None,
) -> str:
    """Update an existing automation rule.

    Returns:
        JSON with the updated rule's 'ruleUuid'.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        result = jira.update_automation_rule(rule_uuid, rule, connections=connections)
        return _admin_ok(action="rule_updated", **result)
    except Exception as e:
        return _admin_error("update_automation_rule", e)


@jira_mcp.tool(
    tags={"jira", "write", "admin"},
    annotations={"title": "Set Automation Rule State", "destructiveHint": True},
)
@check_write_access
async def set_automation_rule_state(
    ctx: Context,
    rule_uuid: Annotated[str, Field(description="UUID of the rule.")],
    state: Annotated[
        str,
        Field(
            description="New state: ENABLED or DISABLED.",
            pattern="^(ENABLED|DISABLED)$",
        ),
    ],
) -> str:
    """Enable or disable an automation rule.

    Returns:
        JSON indicating success or failure.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        jira.set_automation_rule_state(rule_uuid, state)
        return _admin_ok(action="rule_state_set", rule_uuid=rule_uuid, state=state)
    except Exception as e:
        return _admin_error("set_automation_rule_state", e)


# ---------------------------------------------------------------------------
# Filter (saved JQL search) tools.
# ---------------------------------------------------------------------------


@jira_mcp.tool(
    tags={"jira", "read", "admin"},
    annotations={"title": "Search Filters", "readOnlyHint": True},
)
async def search_filters(
    ctx: Context,
    filter_name: Annotated[
        str | None, Field(description="(Optional) Filter name to search.", default=None)
    ] = None,
    substring_match: Annotated[
        bool,
        Field(
            description=(
                "Match anywhere in the name. Without this only names "
                "starting with the text match."
            ),
            default=False,
        ),
    ] = False,
    owner: Annotated[
        str | None, Field(description="(Optional) Owner of the filters.", default=None)
    ] = None,
    order_by: Annotated[
        str | None,
        Field(
            description="(Optional) Sort field, e.g. 'name' or '-name'.",
            default=None,
        ),
    ] = None,
    start_at: Annotated[int, Field(description="Starting index.", default=0, ge=0)] = 0,
    max_results: Annotated[
        int, Field(description="Maximum results.", default=50, ge=1, le=100)
    ] = 50,
) -> str:
    """Search filters (saved JQL searches) by name.

    Returns:
        JSON with 'filters', 'total' and pagination info.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        result = jira.search_filters(
            filter_name,
            substring_match=substring_match,
            owner=owner,
            order_by=order_by,
            start_at=start_at,
            max_results=max_results,
        )
        return _admin_ok(
            filters=result.get("values", []),
            total=result.get("total"),
            start_at=result.get("startAt"),
            max_results=result.get("maxResults"),
            is_last=result.get("isLast"),
        )
    except Exception as e:
        return _admin_error("search_filters", e)


@jira_mcp.tool(
    tags={"jira", "read", "admin"},
    annotations={"title": "Get Filter", "readOnlyHint": True},
)
async def get_filter(
    ctx: Context,
    filter_id: Annotated[str, Field(description="Numeric filter ID.")],
) -> str:
    """Get a filter by ID, including its JQL and share permissions.

    Returns:
        JSON with the filter definition.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        return _admin_ok(filter=jira.get_filter(filter_id))
    except Exception as e:
        return _admin_error("get_filter", e)


@jira_mcp.tool(
    tags={"jira", "read", "admin"},
    annotations={"title": "Get Filter Columns", "readOnlyHint": True},
)
async def get_filter_columns(
    ctx: Context,
    filter_id: Annotated[str, Field(description="Numeric filter ID.")],
) -> str:
    """Get the issue navigator columns configured for a filter.

    Returns:
        JSON with the column list.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        return _admin_ok(columns=jira.get_filter_columns(filter_id))
    except Exception as e:
        return _admin_error("get_filter_columns", e)


@jira_mcp.tool(
    tags={"jira", "write", "admin"},
    annotations={"title": "Create Filter", "destructiveHint": True},
)
@check_write_access
async def create_filter(
    ctx: Context,
    name: Annotated[str, Field(description="Filter name; must be unique.")],
    jql: Annotated[str, Field(description="The JQL query for the filter.")],
    description: Annotated[
        str | None, Field(description="(Optional) Filter description.", default=None)
    ] = None,
    favourite: Annotated[
        bool | None,
        Field(description="(Optional) Select the filter as a favorite.", default=None),
    ] = None,
) -> str:
    """Create a filter (saved JQL search) owned by the current user.

    Returns:
        JSON with the created filter, including its id.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        result = jira.create_filter(
            name, jql, description=description, favourite=favourite
        )
        return _admin_ok(action="filter_created", filter=result)
    except Exception as e:
        return _admin_error("create_filter", e)


@jira_mcp.tool(
    tags={"jira", "write", "admin"},
    annotations={"title": "Update Filter", "destructiveHint": True},
)
@check_write_access
async def update_filter(
    ctx: Context,
    filter_id: Annotated[str, Field(description="Numeric filter ID.")],
    name: Annotated[str, Field(description="The filter's new name.")],
    jql: Annotated[str, Field(description="The filter's new JQL query.")],
    description: Annotated[
        str | None, Field(description="(Optional) New description.", default=None)
    ] = None,
    favourite: Annotated[
        bool | None,
        Field(description="(Optional) Favorite state.", default=None),
    ] = None,
) -> str:
    """Update a filter.

    This is a full replacement: name and JQL must both be supplied even when
    only one of them changes.

    Returns:
        JSON with the updated filter.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        result = jira.update_filter(
            filter_id, name, jql, description=description, favourite=favourite
        )
        return _admin_ok(action="filter_updated", filter=result)
    except Exception as e:
        return _admin_error("update_filter", e)


@jira_mcp.tool(
    tags={"jira", "write", "admin"},
    annotations={"title": "Set Filter Columns", "destructiveHint": True},
)
@check_write_access
async def set_filter_columns(
    ctx: Context,
    filter_id: Annotated[str, Field(description="Numeric filter ID.")],
    columns: Annotated[
        str,
        Field(
            description=(
                "Comma-separated column value names, e.g. "
                "'issuetype,issuekey,summary,status,assignee,updated'. "
                "Replaces the whole column list."
            )
        ),
    ],
) -> str:
    """Set the issue navigator columns for a filter.

    Returns:
        JSON indicating success or failure.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        column_list = _split_csv(columns)
        jira.set_filter_columns(filter_id, column_list)
        return _admin_ok(
            action="filter_columns_set", filter_id=filter_id, columns=column_list
        )
    except Exception as e:
        return _admin_error("set_filter_columns", e)


@jira_mcp.tool(
    tags={"jira", "write", "admin"},
    annotations={"title": "Add Filter Share Permission", "destructiveHint": True},
)
@check_write_access
async def add_filter_share_permission(
    ctx: Context,
    filter_id: Annotated[str, Field(description="Numeric filter ID.")],
    permission_type: Annotated[
        str,
        Field(
            description=(
                "Permission type: user, group, project, projectRole, global "
                "or authenticated. 'global' and 'authenticated' override all "
                "existing permissions."
            ),
            pattern="^(user|group|project|projectRole|global|authenticated)$",
        ),
    ],
    account_id: Annotated[
        str | None, Field(description="(Optional) For 'user'.", default=None)
    ] = None,
    group_name: Annotated[
        str | None, Field(description="(Optional) For 'group'.", default=None)
    ] = None,
    group_id: Annotated[
        str | None, Field(description="(Optional) For 'group'.", default=None)
    ] = None,
    project_id: Annotated[
        str | None,
        Field(description="(Optional) For 'project'/'projectRole'.", default=None),
    ] = None,
    project_role_id: Annotated[
        str | None, Field(description="(Optional) For 'projectRole'.", default=None)
    ] = None,
    rights: Annotated[
        int | None,
        Field(description="(Optional) Bitmask: 1 = view, 2 = edit.", default=None),
    ] = None,
) -> str:
    """Add a share permission to a filter.

    Returns:
        JSON with the created share permission.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        permission = jira.add_filter_share_permission(
            filter_id,
            permission_type,
            account_id=account_id,
            group_name=group_name,
            group_id=group_id,
            project_id=project_id,
            project_role_id=project_role_id,
            rights=rights,
        )
        return _admin_ok(action="share_permission_added", permission=permission)
    except Exception as e:
        return _admin_error("add_filter_share_permission", e)


# ---------------------------------------------------------------------------
# Dashboard and gadget tools.
# ---------------------------------------------------------------------------


@jira_mcp.tool(
    tags={"jira", "read", "admin"},
    annotations={"title": "Search Dashboards", "readOnlyHint": True},
)
async def search_dashboards(
    ctx: Context,
    filter: Annotated[
        str | None, Field(description="(Optional) Name substring.", default=None)
    ] = None,
    start_at: Annotated[int, Field(description="Starting index.", default=0, ge=0)] = 0,
    max_results: Annotated[
        int, Field(description="Maximum results.", default=50, ge=1, le=100)
    ] = 50,
) -> str:
    """Search dashboards by name substring.

    Returns:
        JSON with 'dashboards' and pagination info.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        result = jira.search_dashboards(
            filter, start_at=start_at, max_results=max_results
        )
        return _admin_ok(
            dashboards=result.get("values", []),
            total=result.get("total"),
            start_at=result.get("startAt"),
            max_results=result.get("maxResults"),
            is_last=result.get("isLast"),
        )
    except Exception as e:
        return _admin_error("search_dashboards", e)


@jira_mcp.tool(
    tags={"jira", "read", "admin"},
    annotations={"title": "Get Dashboard", "readOnlyHint": True},
)
async def get_dashboard(
    ctx: Context,
    dashboard_id: Annotated[str, Field(description="Numeric dashboard ID.")],
) -> str:
    """Get a dashboard's details.

    Returns:
        JSON with name, description, owner and permissions.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        return _admin_ok(dashboard=jira.get_dashboard(dashboard_id))
    except Exception as e:
        return _admin_error("get_dashboard", e)


@jira_mcp.tool(
    tags={"jira", "read", "admin"},
    annotations={"title": "Get Dashboard Gadgets", "readOnlyHint": True},
)
async def get_dashboard_gadgets(
    ctx: Context,
    dashboard_id: Annotated[str, Field(description="Numeric dashboard ID.")],
) -> str:
    """Get the gadgets on a dashboard.

    Returns:
        JSON with 'gadgets' (id, moduleKey/uri, title, position). The gadget
        id is needed as item id for gadget configuration.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        return _admin_ok(gadgets=jira.get_dashboard_gadgets(dashboard_id))
    except Exception as e:
        return _admin_error("get_dashboard_gadgets", e)


@jira_mcp.tool(
    tags={"jira", "read", "admin"},
    annotations={"title": "Get Available Dashboard Gadgets", "readOnlyHint": True},
)
async def get_available_dashboard_gadgets(
    ctx: Context,
) -> str:
    """List the gadget types that can be added to dashboards.

    Returns:
        JSON with 'gadgets' (uri or moduleKey, title).
    """
    jira = await get_jira_fetcher(ctx)
    try:
        return _admin_ok(gadgets=jira.get_available_dashboard_gadgets())
    except Exception as e:
        return _admin_error("get_available_dashboard_gadgets", e)


@jira_mcp.tool(
    tags={"jira", "read", "admin"},
    annotations={"title": "Get Gadget Config", "readOnlyHint": True},
)
async def get_gadget_config(
    ctx: Context,
    dashboard_id: Annotated[str, Field(description="Numeric dashboard ID.")],
    item_id: Annotated[
        str,
        Field(
            description=(
                "Gadget ID from get_dashboard_gadgets (the gadget instance id)."
            )
        ),
    ],
) -> str:
    """Get all stored configuration properties of a dashboard gadget.

    Returns:
        JSON with the properties. Filter Results gadgets store their
        settings under 'config' (filterId, columnNames, num, ...).
    """
    jira = await get_jira_fetcher(ctx)
    try:
        return _admin_ok(config=jira.get_gadget_config(dashboard_id, item_id))
    except Exception as e:
        return _admin_error("get_gadget_config", e)


@jira_mcp.tool(
    tags={"jira", "write", "admin"},
    annotations={"title": "Create Dashboard", "destructiveHint": True},
)
@check_write_access
async def create_dashboard(
    ctx: Context,
    name: Annotated[str, Field(description="Dashboard name.")],
    description: Annotated[
        str | None, Field(description="(Optional) Dashboard description.", default=None)
    ] = None,
    share_permissions: Annotated[
        list[dict[str, Any]] | None,
        Field(
            description=(
                "(Optional) Share permissions as JSON, e.g. "
                '[{"type": "authenticated"}]. Empty (default) creates a '
                "private dashboard."
            ),
            default=None,
        ),
    ] = None,
    edit_permissions: Annotated[
        list[dict[str, Any]] | None,
        Field(
            description=(
                "(Optional) Edit permissions as JSON; defaults to the share "
                "permissions."
            ),
            default=None,
        ),
    ] = None,
) -> str:
    """Create a dashboard owned by the current user.

    Returns:
        JSON with the created dashboard, including its id.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        result = jira.create_dashboard(
            name,
            description=description,
            share_permissions=share_permissions,
            edit_permissions=edit_permissions,
        )
        return _admin_ok(action="dashboard_created", dashboard=result)
    except Exception as e:
        return _admin_error("create_dashboard", e)


@jira_mcp.tool(
    tags={"jira", "write", "admin"},
    annotations={"title": "Add Dashboard Gadget", "destructiveHint": True},
)
@check_write_access
async def add_gadget(
    ctx: Context,
    dashboard_id: Annotated[str, Field(description="Numeric dashboard ID.")],
    uri: Annotated[
        str | None,
        Field(
            description=(
                "(Optional) URI of the gadget type, e.g. "
                "'rest/gadgets/1.0/g/com.atlassian.jira.gadgets:filter-results-gadget/gadgets/filter-results-gadget.xml'. "
                "Exactly one of uri or module_key."
            ),
            default=None,
        ),
    ] = None,
    module_key: Annotated[
        str | None,
        Field(
            description=(
                "(Optional) Module key of the gadget type; alternative to uri."
            ),
            default=None,
        ),
    ] = None,
    title: Annotated[
        str | None,
        Field(
            description="(Optional) Gadget title; defaults to the type's.", default=None
        ),
    ] = None,
    color: Annotated[
        str | None, Field(description="(Optional) Gadget color.", default=None)
    ] = None,
    column: Annotated[
        int | None,
        Field(description="(Optional) 0-based column position.", default=None),
    ] = None,
    row: Annotated[
        int | None,
        Field(
            description=(
                "(Optional) Row position; other gadgets in the column move down."
            ),
            default=None,
        ),
    ] = None,
) -> str:
    """Add a gadget to a dashboard.

    Use get_available_dashboard_gadgets to find the uri/module_key of gadget
    types.

    Returns:
        JSON with the created gadget, including its id.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        gadget = jira.add_gadget(
            dashboard_id,
            uri=uri,
            module_key=module_key,
            title=title,
            color=color,
            column=column,
            row=row,
        )
        return _admin_ok(action="gadget_added", gadget=gadget)
    except Exception as e:
        return _admin_error("add_gadget", e)


@jira_mcp.tool(
    tags={"jira", "write", "admin"},
    annotations={"title": "Update Dashboard Gadget", "destructiveHint": True},
)
@check_write_access
async def update_gadget(
    ctx: Context,
    dashboard_id: Annotated[str, Field(description="Numeric dashboard ID.")],
    gadget_id: Annotated[str, Field(description="The gadget's numeric ID.")],
    title: Annotated[
        str | None, Field(description="(Optional) New title.", default=None)
    ] = None,
    color: Annotated[
        str | None, Field(description="(Optional) New color.", default=None)
    ] = None,
    column: Annotated[
        int | None, Field(description="(Optional) New column position.", default=None)
    ] = None,
    row: Annotated[
        int | None, Field(description="(Optional) New row position.", default=None)
    ] = None,
) -> str:
    """Update a dashboard gadget's title, color and/or position.

    Returns:
        JSON indicating success or failure.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        jira.update_gadget(
            dashboard_id,
            gadget_id,
            title=title,
            color=color,
            column=column,
            row=row,
        )
        return _admin_ok(
            action="gadget_updated", dashboard_id=dashboard_id, gadget_id=gadget_id
        )
    except Exception as e:
        return _admin_error("update_gadget", e)


@jira_mcp.tool(
    tags={"jira", "write", "admin"},
    annotations={"title": "Set Gadget Config", "destructiveHint": True},
)
@check_write_access
async def set_gadget_config(
    ctx: Context,
    dashboard_id: Annotated[str, Field(description="Numeric dashboard ID.")],
    item_id: Annotated[
        str,
        Field(
            description=(
                "Gadget ID from get_dashboard_gadgets (the gadget instance id)."
            )
        ),
    ],
    config: Annotated[
        dict[str, Any],
        Field(
            description=(
                "The configuration value to store as JSON, e.g. "
                '{"filterId": "12345", "isConfigured": "true", '
                '"num": "50"} for a Filter Results gadget. Jira expects '
                "the values as strings."
            )
        ),
    ],
    property_key: Annotated[
        str,
        Field(
            description="Property key under which the config is stored.",
            default="config",
        ),
    ] = "config",
) -> str:
    """Store a dashboard gadget's configuration.

    Inspect an existing gadget with get_gadget_config for the exact keys a
    gadget type uses.

    Returns:
        JSON indicating success or failure.
    """
    jira = await get_jira_fetcher(ctx)
    try:
        jira.set_gadget_config(dashboard_id, item_id, config, property_key=property_key)
        return _admin_ok(
            action="gadget_config_set",
            dashboard_id=dashboard_id,
            item_id=item_id,
            property_key=property_key,
        )
    except Exception as e:
        return _admin_error("set_gadget_config", e)
