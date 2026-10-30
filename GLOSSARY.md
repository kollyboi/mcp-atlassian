# Jira MCP Server

MCP server exposing Jira and Confluence APIs as AI agent tools for managing projects, permissions, issues, and content.

## Language

**Dashboard Owner**:
The Jira user who owns a dashboard, distinct from users allowed to view or edit it.
_Avoid_: dashboard editor, dashboard administrator

**Atlassian Account ID**:
The identifier of an Atlassian Cloud user account, distinct from an email address,
display name, or Jira Server/Data Center username.
_Avoid_: username, email, user key

**Permission Scheme**:
A site-wide configuration that maps Jira permissions to roles, groups, or specific holders, and can be assigned to one or more projects.
_Avoid_: security scheme, access list

**Permission Grant**:
A single mapping within a permission scheme authorizing a specific holder to perform a Jira action.
_Avoid_: permission rule, permission entry, entitlement

**Project Role**:
A role defined at the site level whose membership is configured per project.
_Avoid_: team, project group, global role

**Role Actor**:
A specific user or group assigned to a project role within a particular project.
_Avoid_: role member, project member, participant, assignee

**Jira Site API Token**:
A user-scoped credential used alongside a username/email with HTTP Basic Auth against a specific Jira Cloud site URL (e.g. `*.atlassian.net/rest/api/3/`) to manage project, issue, screen, and permission configurations.
_Avoid_: org API key, Atlassian token, admin key

**Organization Admin API Key**:
An organization-scoped API key created in Atlassian Administration (`admin.atlassian.com`) and transmitted as an HTTP Bearer token against `api.atlassian.com/admin/v2/` to manage enterprise users, directories, and cross-site policies.
_Avoid_: Jira API token, personal token, site key

**Organization Directory ID**:
The unique identifier of an Atlassian organization user directory. Required for user lifecycle mutations (suspend, restore, remove); wildcard `"-"` is accepted by Atlassian only for read/search endpoints.
_Avoid_: site ID, cloud ID, wildcard directory

**Entity Property**:
Arbitrary key-value JSON metadata stored directly on a Jira entity (such as an issue, project, or dashboard item), typically set by Atlassian apps or integrations and distinct from issue fields.
_Avoid_: custom field, entity attribute, app config

**Effective Permission**:
The dynamically evaluated access right that an individual user currently possesses for a specific project or issue, calculated by Jira from permission schemes, project roles, group memberships, and issue security.
_Avoid_: permission grant, static entitlement, scheme entry
