#!/usr/bin/env python3
"""
import_project.py
-----------------
Reads a structured data/ directory and bootstraps a GitHub repository with:
  - GitHub Project V2 with custom fields and views
  - Milestones, labels, and issues
  - Issue templates pushed to .github/ISSUE_TEMPLATE/

Usage:
    python import_project.py [path/to/data/]

Defaults to "data/" in the current directory.

Data directory layout:
    data/
      project.json        Project metadata
      fields.json         Custom field definitions
      views.json          View definitions
      labels.json         Label definitions
      milestones.json     Milestone list
      tickets/            One JSON file per milestone group
        *.json
      templates/          Issue template YAML forms
        *.yml
"""

import base64
import json
import os
import sys
import time
from pathlib import Path

import requests
from dotenv import load_dotenv

# ---------------------------------------------------------------------------
# ANSI color helpers
# ---------------------------------------------------------------------------

RESET  = "\033[0m"
BOLD   = "\033[1m"
GREEN  = "\033[32m"
YELLOW = "\033[33m"
RED    = "\033[31m"
CYAN   = "\033[36m"
DIM    = "\033[2m"


def ok(msg: str)   -> None: print(f"  {GREEN}✅ {msg}{RESET}")
def warn(msg: str) -> None: print(f"  {YELLOW}⚠️  {msg}{RESET}")
def err(msg: str)  -> None: print(f"  {RED}❌ {msg}{RESET}")
def info(msg: str) -> None: print(f"  {CYAN}ℹ️  {msg}{RESET}")
def step(msg: str) -> None: print(f"\n{BOLD}{msg}{RESET}")
def dim(msg: str)  -> None: print(f"  {DIM}{msg}{RESET}")


# ---------------------------------------------------------------------------
# Data loader
# ---------------------------------------------------------------------------

def load_data_dir(data_dir: Path) -> dict:
    """
    Load all project data from the data/ directory.

    Returns a dict with keys:
      project, fields, views, labels, milestones, tickets, templates
    """

    def read_json(path: Path):
        if not path.exists():
            return None
        with path.open(encoding="utf-8") as fh:
            return json.load(fh)

    required = ["project.json", "milestones.json"]
    for name in required:
        if not (data_dir / name).exists():
            err(f"Required file not found: {data_dir / name}")
            sys.exit(1)

    result = {
        "project":    read_json(data_dir / "project.json"),
        "fields":     read_json(data_dir / "fields.json")    or [],
        "views":      read_json(data_dir / "views.json")     or [],
        "labels":     read_json(data_dir / "labels.json")    or [],
        "milestones": read_json(data_dir / "milestones.json"),
        "tickets":    [],
        "templates":  {},
    }

    # Load tickets from all files in tickets/, sorted by filename
    tickets_dir = data_dir / "tickets"
    if tickets_dir.is_dir():
        for ticket_file in sorted(tickets_dir.glob("*.json")):
            file_data = read_json(ticket_file)
            if file_data:
                result["tickets"].extend(file_data.get("tickets", []))
        info(f"Loaded {len(result['tickets'])} tickets from {tickets_dir}")

    # Load templates as raw YAML strings keyed by filename
    templates_dir = data_dir / "templates"
    if templates_dir.is_dir():
        for tmpl_file in sorted(templates_dir.glob("*.yml")):
            result["templates"][tmpl_file.name] = tmpl_file.read_text(encoding="utf-8")
        info(f"Loaded {len(result['templates'])} issue templates from {templates_dir}")

    return result


# ---------------------------------------------------------------------------
# Environment & config
# ---------------------------------------------------------------------------

def load_config() -> dict:
    """Load and validate required environment variables."""
    load_dotenv()

    required = ["GITHUB_TOKEN", "REPO_OWNER", "REPO_NAME"]
    config = {key: os.getenv(key) for key in required}

    missing = [k for k, v in config.items() if not v]
    if missing:
        err(f"Missing required environment variables: {', '.join(missing)}")
        err("Copy .env.example to .env and fill in your values.")
        sys.exit(1)

    return config


# ---------------------------------------------------------------------------
# HTTP session with retry logic
# ---------------------------------------------------------------------------

def build_session(token: str) -> requests.Session:
    """Return a requests Session pre-configured with auth headers."""
    session = requests.Session()
    session.headers.update({
        "Authorization": f"Bearer {token}",
        "Accept": "application/vnd.github+json",
        "X-GitHub-Api-Version": "2022-11-28",
    })
    return session


def request_with_retry(
    session: requests.Session,
    method: str,
    url: str,
    *,
    max_retries: int = 5,
    **kwargs,
) -> requests.Response:
    """
    Execute an HTTP request and transparently handle GitHub rate limits.

    Retries on:
      - 429  Too Many Requests
      - 403  with a Retry-After header (secondary rate limit)
    """
    for attempt in range(1, max_retries + 1):
        response = session.request(method, url, **kwargs)

        is_primary_rate   = response.status_code == 429
        is_secondary_rate = (
            response.status_code == 403
            and "retry-after" in response.headers
        )

        if is_primary_rate or is_secondary_rate:
            retry_after = int(response.headers.get("Retry-After", 60))
            warn(
                f"Rate limited (HTTP {response.status_code}). "
                f"Waiting {retry_after}s before retry {attempt}/{max_retries}…"
            )
            time.sleep(retry_after + 1)
            continue

        return response

    warn(f"Exhausted {max_retries} retries for {method.upper()} {url}")
    return response


# ---------------------------------------------------------------------------
# REST API helpers
# ---------------------------------------------------------------------------

def rest_post(session, url, payload):
    """POST JSON to the REST API. Returns parsed JSON or None on failure."""
    response = request_with_retry(session, "POST", url, json=payload)
    if response.status_code in (200, 201):
        return response.json()

    err(f"REST POST {url} → HTTP {response.status_code}")
    dim(response.text[:400])
    return None


def rest_get(session, url, params=None):
    """GET from the REST API. Returns parsed JSON or None on failure."""
    response = request_with_retry(session, "GET", url, params=params)
    if response.status_code == 200:
        return response.json()
    if response.status_code == 404:
        return None  # Not found is a valid state, not an error

    err(f"REST GET {url} → HTTP {response.status_code}")
    dim(response.text[:400])
    return None


def rest_put(session, url, payload):
    """PUT JSON to the REST API. Returns parsed JSON or None on failure."""
    response = request_with_retry(session, "PUT", url, json=payload)
    if response.status_code in (200, 201):
        return response.json()

    err(f"REST PUT {url} → HTTP {response.status_code}")
    dim(response.text[:400])
    return None


# ---------------------------------------------------------------------------
# GraphQL helper
# ---------------------------------------------------------------------------

GRAPHQL_URL = "https://api.github.com/graphql"


def graphql(session, query: str, variables: dict):
    """Execute a GraphQL query/mutation. Returns the 'data' dict or None."""
    response = request_with_retry(
        session,
        "POST",
        GRAPHQL_URL,
        json={"query": query, "variables": variables},
    )

    if response.status_code != 200:
        err(f"GraphQL HTTP {response.status_code}")
        dim(response.text[:400])
        return None

    body = response.json()
    if "errors" in body:
        err("GraphQL errors:")
        for e in body["errors"]:
            dim(f"  • {e.get('message', e)}")
        return None

    return body.get("data")


# ---------------------------------------------------------------------------
# Step 0 — Create GitHub Project V2
# ---------------------------------------------------------------------------

_OWNER_USER_QUERY = """
query GetUser($login: String!) {
  user(login: $login) {
    id
    login
    projectsV2(first: 100) {
      nodes {
        id
        title
        url
      }
    }
  }
}
"""

_OWNER_ORG_QUERY = """
query GetOrg($login: String!) {
  organization(login: $login) {
    id
    login
    projectsV2(first: 100) {
      nodes {
        id
        title
        url
      }
    }
  }
}
"""

_CREATE_PROJECT_MUTATION = """
mutation CreateProject($ownerId: ID!, $title: String!) {
  createProjectV2(input: { ownerId: $ownerId, title: $title }) {
    projectV2 {
      id
      title
      url
    }
  }
}
"""


def resolve_owner_id(session, owner_login: str) -> tuple[str, list]:
    """
    Return (graphql_node_id, existing_projects_list) for a GitHub user or org.
    existing_projects_list is a list of dicts with id/title/url.
    Exits the program if neither user nor org resolves.
    """
    data = graphql(session, _OWNER_USER_QUERY, {"login": owner_login})
    if data and data.get("user"):
        node_id  = data["user"]["id"]
        projects = data["user"].get("projectsV2", {}).get("nodes", [])
        dim(f"Owner resolved as user: {owner_login} ({node_id})")
        return node_id, projects

    data = graphql(session, _OWNER_ORG_QUERY, {"login": owner_login})
    if data and data.get("organization"):
        node_id  = data["organization"]["id"]
        projects = data["organization"].get("projectsV2", {}).get("nodes", [])
        dim(f"Owner resolved as organization: {owner_login} ({node_id})")
        return node_id, projects

    err(f'Could not resolve "{owner_login}" as a GitHub user or organization.')
    err("Check that REPO_OWNER in your .env is correct and your token has the right scopes.")
    sys.exit(1)


def create_project_v2(session, owner_login: str, title: str) -> str:
    """
    Return a GitHub Project V2 node ID for the given owner and title.
    Reuses an existing project with the same title instead of creating a duplicate.
    """
    step("Step 0 — Creating GitHub Project V2")

    owner_id, existing_projects = resolve_owner_id(session, owner_login)

    for proj in existing_projects:
        if proj.get("title") == title:
            warn(f'Project already exists: "{title}" — reusing it')
            dim(f'  {proj["url"]}')
            dim(f'  Node ID: {proj["id"]}')
            return proj["id"]

    data = graphql(session, _CREATE_PROJECT_MUTATION, {"ownerId": owner_id, "title": title})
    if not data or not data.get("createProjectV2", {}).get("projectV2"):
        err(f'Failed to create project "{title}".')
        err("Ensure your token has the 'project' scope (read:project + write:project).")
        sys.exit(1)

    project = data["createProjectV2"]["projectV2"]
    ok(f'Created Project V2: "{project["title"]}"')
    dim(f'  {project["url"]}')
    dim(f'  Node ID: {project["id"]}')

    return project["id"]


# ---------------------------------------------------------------------------
# Step 1 — Custom Fields
# ---------------------------------------------------------------------------

_GET_PROJECT_FIELDS_QUERY = """
query GetProjectFields($projectId: ID!) {
  node(id: $projectId) {
    ... on ProjectV2 {
      fields(first: 50) {
        nodes {
          ... on ProjectV2Field {
            id
            name
            dataType
          }
          ... on ProjectV2SingleSelectField {
            id
            name
            dataType
            options {
              id
              name
            }
          }
          ... on ProjectV2IterationField {
            id
            name
            dataType
          }
        }
      }
    }
  }
}
"""

_CREATE_FIELD_MUTATION = """
mutation CreateField(
  $projectId: ID!,
  $dataType: ProjectV2CustomFieldType!,
  $name: String!,
  $options: [ProjectV2SingleSelectFieldOptionInput!]
) {
  createProjectV2Field(input: {
    projectId: $projectId,
    dataType: $dataType,
    name: $name,
    singleSelectOptions: $options
  }) {
    projectV2Field {
      ... on ProjectV2Field {
        id
        name
        dataType
      }
      ... on ProjectV2SingleSelectField {
        id
        name
        dataType
        options {
          id
          name
        }
      }
    }
  }
}
"""

_UPDATE_FIELD_VALUE_MUTATION = """
mutation UpdateFieldValue(
  $projectId: ID!,
  $itemId: ID!,
  $fieldId: ID!,
  $value: ProjectV2FieldValue!
) {
  updateProjectV2ItemFieldValue(input: {
    projectId: $projectId,
    itemId: $itemId,
    fieldId: $fieldId,
    value: $value
  }) {
    projectV2Item {
      id
    }
  }
}
"""


def _field_key(name: str) -> str:
    """Convert a field name to a snake_case dict key. e.g. 'Start Date' -> 'start_date'"""
    return name.lower().replace(" ", "_")


def setup_project_fields(session, project_id: str, field_defs: list) -> dict:
    """
    Ensure all custom fields defined in fields.json exist on the project.

    Returns a dict keyed by snake_case(field_name) with the structure:
    {
      "component": {"id": "...", "kind": "singleSelect", "options": {"Auth": "...", ...}},
      "start_date": {"id": "...", "kind": "date"},
      ...
    }
    """
    step("Step 1 — Setting up Project V2 custom fields")

    if not field_defs:
        warn("No field definitions found — skipping custom fields setup.")
        return {}

    data = graphql(session, _GET_PROJECT_FIELDS_QUERY, {"projectId": project_id})
    if not data:
        warn("Could not fetch project fields — custom field values will not be set.")
        return {}

    # Index existing fields by name
    existing: dict[str, dict] = {}
    for node in data["node"]["fields"]["nodes"]:
        if not node:
            continue
        name = node.get("name", "")
        if "options" in node:
            existing[name] = {
                "id": node["id"],
                "kind": "singleSelect",
                "options": {o["name"]: o["id"] for o in node["options"]},
            }
        else:
            existing[name] = {"id": node["id"], "kind": "unknown"}

    # Map GitHub data type string → internal kind
    _KIND_MAP = {
        "DATE":          "date",
        "NUMBER":        "number",
        "TEXT":          "text",
        "SINGLE_SELECT": "singleSelect",
        "ITERATION":     "iteration",
    }

    result: dict[str, dict] = {}

    for field_def in field_defs:
        field_name = field_def["name"]
        data_type  = field_def["data_type"]
        key        = _field_key(field_name)
        kind       = _KIND_MAP.get(data_type, "unknown")

        # Iteration fields cannot be created via the API — skip with notice
        if data_type == "ITERATION":
            warn(f'Skipping "{field_name}" — iteration fields must be created manually in GitHub UI.')
            continue

        if field_name in existing:
            info(f'Field already exists: "{field_name}"')
            entry = existing[field_name]
            entry["kind"] = kind  # ensure kind is set correctly
            result[key] = entry
            continue

        variables: dict = {
            "projectId": project_id,
            "dataType":  data_type,
            "name":      field_name,
        }
        if data_type == "SINGLE_SELECT":
            options = field_def.get("options", [])
            variables["options"] = [
                {
                    "name":        opt["name"],
                    "color":       opt.get("color", "GRAY"),
                    "description": opt.get("description", ""),
                }
                for opt in options
            ]

        create_data = graphql(session, _CREATE_FIELD_MUTATION, variables)
        if not create_data:
            warn(f'Failed to create field "{field_name}"')
            continue

        created = create_data.get("createProjectV2Field", {}).get("projectV2Field")
        if not created:
            warn(f'No field returned when creating "{field_name}"')
            continue

        entry: dict = {"id": created["id"], "kind": kind}
        if kind == "singleSelect":
            entry["options"] = {o["name"]: o["id"] for o in created.get("options", [])}

        result[key] = entry
        ok(f'Created field: "{field_name}"')

    return result


# ---------------------------------------------------------------------------
# Step 2 — Views
# ---------------------------------------------------------------------------

_GET_PROJECT_VIEWS_QUERY = """
query GetProjectViews($projectId: ID!) {
  node(id: $projectId) {
    ... on ProjectV2 {
      views(first: 50) {
        nodes {
          id
          name
          layout
        }
      }
    }
  }
}
"""

_CREATE_VIEW_MUTATION = """
mutation CreateView($projectId: ID!, $name: String!, $layout: ProjectV2ViewLayout!) {
  createProjectV2View(input: {
    projectId: $projectId
    name: $name
    layout: $layout
  }) {
    projectV2View {
      id
      name
      layout
    }
  }
}
"""

_UPDATE_VIEW_MUTATION = """
mutation UpdateView(
  $projectId: ID!,
  $viewId: ID!,
  $filter: String,
  $groupByFieldIds: [ID!],
  $sortByFields: [ProjectV2ViewSortByField!]
) {
  updateProjectV2View(input: {
    projectId: $projectId
    viewId: $viewId
    filter: $filter
    groupByFields: $groupByFieldIds
    sortByFields: $sortByFields
  }) {
    projectV2View {
      id
      name
    }
  }
}
"""


def setup_project_views(session, project_id: str, view_defs: list, fields: dict) -> None:
    """
    Ensure all views defined in views.json exist on the project.
    Creates views that don't exist and applies filter/group-by configuration.
    """
    step("Step 2 — Setting up Project V2 views")

    if not view_defs:
        info("No view definitions found — skipping views setup.")
        return

    # Fetch existing views for idempotency
    views_data = graphql(session, _GET_PROJECT_VIEWS_QUERY, {"projectId": project_id})
    existing_views: dict[str, str] = {}  # name -> id
    if views_data:
        for node in views_data["node"]["views"]["nodes"]:
            if node:
                existing_views[node["name"]] = node["id"]

    for view_def in view_defs:
        name   = view_def["name"]
        layout = view_def["layout"]

        if name in existing_views:
            warn(f'View already exists: "{name}" — skipping')
            continue

        # Create the view
        create_data = graphql(
            session,
            _CREATE_VIEW_MUTATION,
            {"projectId": project_id, "name": name, "layout": layout},
        )
        if not create_data:
            warn(f'Failed to create view "{name}"')
            continue

        view = create_data.get("createProjectV2View", {}).get("projectV2View")
        if not view:
            warn(f'No view returned when creating "{name}"')
            continue

        view_id = view["id"]
        ok(f'Created view: "{name}" ({layout})')

        # Apply filter and group-by
        filter_str    = view_def.get("filter")
        group_by_name = view_def.get("group_by")

        group_by_ids = None
        if group_by_name:
            group_field = fields.get(_field_key(group_by_name))
            if group_field:
                group_by_ids = [group_field["id"]]
            else:
                warn(f'  Group-by field "{group_by_name}" not found in fields — skipping')

        if filter_str or group_by_ids:
            update_vars: dict = {
                "projectId":      project_id,
                "viewId":         view_id,
                "filter":         filter_str,
                "groupByFieldIds": group_by_ids,
                "sortByFields":   None,
            }
            upd = graphql(session, _UPDATE_VIEW_MUTATION, update_vars)
            if upd:
                parts = []
                if filter_str:   parts.append(f"filter={filter_str!r}")
                if group_by_ids: parts.append(f"group_by={group_by_name}")
                dim(f"  Applied: {', '.join(parts)}")
            else:
                warn(f'  Could not configure view "{name}" — view was created but filter/group-by not applied.')

        time.sleep(0.3)  # brief pause between view creations


# ---------------------------------------------------------------------------
# Step 3 — Milestones
# ---------------------------------------------------------------------------

def sync_milestones(session, base_url: str, milestones: list) -> dict:
    """
    Create milestones from the JSON. Return a mapping of local milestone
    IDs (e.g. "M0") to GitHub milestone numbers.
    """
    step("Step 3 — Syncing Milestones")

    # Fetch existing milestones to avoid duplicates.
    existing_raw = rest_get(session, f"{base_url}/milestones", params={"state": "all", "per_page": 100}) or []
    existing = {m["title"]: m["number"] for m in existing_raw}

    id_to_number: dict[str, int] = {}

    for ms in milestones:
        local_id = ms["id"]
        title    = ms["title"]

        if title in existing:
            number = existing[title]
            warn(f'Milestone already exists: "{title}" (#{number}) — skipping')
            id_to_number[local_id] = number
            continue

        payload = {"title": title, "description": ms.get("description", "")}
        data = rest_post(session, f"{base_url}/milestones", payload)

        if data:
            number = data["number"]
            id_to_number[local_id] = number
            ok(f'Created milestone: "{title}" (#{number})')
        else:
            err(f'Failed to create milestone: "{title}"')

    return id_to_number


# ---------------------------------------------------------------------------
# Step 4 — Labels
# ---------------------------------------------------------------------------

def ensure_labels(session, base_url: str, label_defs: list) -> None:
    """
    Create labels defined in labels.json. Each label has name, color, description.
    Only creates missing labels; updates color/description of existing ones if different.
    """
    step("Step 4 — Ensuring Labels Exist")

    # Fetch existing labels (paginate up to 300).
    existing_labels: dict[str, dict] = {}
    for page in range(1, 4):
        page_data = rest_get(session, f"{base_url}/labels", params={"per_page": 100, "page": page}) or []
        if not page_data:
            break
        for lbl in page_data:
            existing_labels[lbl["name"]] = lbl

    for label in label_defs:
        name        = label["name"]
        color       = label.get("color", "cfd3d7")
        description = label.get("description", "")

        if name in existing_labels:
            warn(f'Label already exists: "{name}" — skipping')
            continue

        data = rest_post(session, f"{base_url}/labels", {
            "name":        name,
            "color":       color,
            "description": description,
        })
        if data:
            ok(f'Created label: "{name}"')
        else:
            warn(f'Could not create label "{name}" — it may already exist.')


# ---------------------------------------------------------------------------
# Step 5 — Issue Templates
# ---------------------------------------------------------------------------

def push_issue_templates(session, owner: str, repo: str, templates: dict) -> None:
    """
    Push issue template YAML files to .github/ISSUE_TEMPLATE/ in the repo.
    Creates or updates each file via the GitHub Contents API.
    """
    step("Step 5 — Pushing Issue Templates to Repository")

    if not templates:
        info("No templates to push.")
        return

    base = f"https://api.github.com/repos/{owner}/{repo}/contents"

    for filename, content in templates.items():
        path     = f".github/ISSUE_TEMPLATE/{filename}"
        api_url  = f"{base}/{path}"
        encoded  = base64.b64encode(content.encode("utf-8")).decode("ascii")

        # Check if file already exists (need its SHA to update it)
        existing = rest_get(session, api_url)
        payload: dict = {
            "message": f"chore: add issue template {filename}",
            "content": encoded,
        }
        if existing and isinstance(existing, dict) and "sha" in existing:
            payload["sha"] = existing["sha"]
            payload["message"] = f"chore: update issue template {filename}"
            action = "Updated"
        else:
            action = "Created"

        result = rest_put(session, api_url, payload)
        if result:
            ok(f"{action} template: {path}")
        else:
            warn(f"Failed to push template: {path}")

        time.sleep(0.5)  # avoid secondary rate limits on content writes


# ---------------------------------------------------------------------------
# Step 6 — Issues
# ---------------------------------------------------------------------------

_ADD_ITEM_MUTATION = """
mutation AddItemToProject($projectId: ID!, $contentId: ID!) {
  addProjectV2ItemById(input: { projectId: $projectId, contentId: $contentId }) {
    item {
      id
    }
  }
}
"""


def build_issue_body(ticket: dict) -> str:
    """Render a rich Markdown body from a ticket dict."""
    lines: list[str] = []

    if description := ticket.get("description", "").strip():
        lines += ["## Description", "", description, ""]

    if tasks := ticket.get("tasks", []):
        lines += ["## Tasks", ""]
        lines += [f"- [ ] {t}" for t in tasks]
        lines.append("")

    if criteria := ticket.get("acceptance_criteria", []):
        lines += ["## Acceptance Criteria", ""]
        lines += [f"- [ ] {c}" for c in criteria]
        lines.append("")

    if goals := ticket.get("learning_goals", []):
        lines += ["## Learning Goals", ""]
        lines += [f"- {g}" for g in goals]
        lines.append("")

    # Metadata footer
    meta: list[str] = []
    if start := ticket.get("start_date"):
        meta.append(f"📅 **Start:** {start}")
    if end := ticket.get("end_date"):
        meta.append(f"🏁 **Due:** {end}")
    if assignees := ticket.get("assignees", []):
        meta.append(f"👤 **Assigned to:** {', '.join(f'@{a}' for a in assignees)}")
    if ticket_id := ticket.get("id"):
        meta.append(f"🔖 **Ticket ID:** `{ticket_id}`")
    if depends_on := ticket.get("depends_on", []):
        meta.append(f"🔗 **Depends on:** {', '.join(f'`{d}`' for d in depends_on)}")

    if meta:
        lines += ["---", *meta]

    return "\n".join(lines).strip()


def _resolve_option_id(field_options: dict, raw_value: str) -> str | None:
    """
    Find a single-select option ID for a raw string value.
    Tries exact match, then title-case, then case-insensitive.
    """
    if raw_value in field_options:
        return field_options[raw_value]
    title_val = raw_value.title()
    if title_val in field_options:
        return field_options[title_val]
    lower_map = {k.lower(): v for k, v in field_options.items()}
    return lower_map.get(raw_value.lower())


def _update_item_fields(
    session,
    project_id: str,
    item_id: str,
    ticket: dict,
    fields: dict,
    issue_number: int,
) -> None:
    """Set all configured field values on a project item from the ticket data."""
    for field_key, field_info in fields.items():
        value = ticket.get(field_key)
        if value is None:
            continue

        kind = field_info["kind"]

        if kind == "date":
            gql_value = {"date": str(value)}
        elif kind == "number":
            gql_value = {"number": float(value)}
        elif kind == "singleSelect":
            option_id = _resolve_option_id(field_info.get("options", {}), str(value))
            if not option_id:
                warn(f'Unknown option "{value}" for field "{field_key}" on issue #{issue_number} — skipping')
                continue
            gql_value = {"singleSelectOptionId": option_id}
        else:
            continue

        upd = graphql(
            session,
            _UPDATE_FIELD_VALUE_MUTATION,
            {
                "projectId": project_id,
                "itemId":    item_id,
                "fieldId":   field_info["id"],
                "value":     gql_value,
            },
        )
        if not upd:
            warn(f'Could not set "{field_key}" on issue #{issue_number}')


def create_issues(
    session,
    base_url: str,
    tickets: list,
    milestone_map: dict,
    project_id: str,
    fields: dict,
) -> None:
    """Create each ticket as a GitHub Issue and link it to the Project V2."""
    step("Step 6 — Creating Issues & Linking to Project V2")

    # Fetch all existing issues (open + closed) to detect duplicates by title.
    info("Fetching existing issues for duplicate detection…")
    existing_issues: dict[str, dict] = {}
    page = 1
    while True:
        page_data = rest_get(
            session,
            f"{base_url}/issues",
            params={"state": "all", "per_page": 100, "page": page},
        ) or []
        if not page_data:
            break
        for issue in page_data:
            existing_issues[issue["title"]] = issue
        if len(page_data) < 100:
            break
        page += 1

    if existing_issues:
        info(f"Found {len(existing_issues)} existing issue(s) — duplicates will be skipped.")

    total = len(tickets)
    for idx, ticket in enumerate(tickets, start=1):
        title = ticket.get("title", f"Untitled Ticket {idx}")
        print(f"\n  [{idx}/{total}] {BOLD}{title}{RESET}")

        if title in existing_issues:
            existing      = existing_issues[title]
            issue_number  = existing["number"]
            issue_node_id = existing["node_id"]
            issue_url     = existing["html_url"]
            warn(f"Issue already exists: #{issue_number} — skipping creation")
            dim(f"  {issue_url}")
        else:
            body             = build_issue_body(ticket)
            local_milestone  = ticket.get("milestone")
            milestone_number = milestone_map.get(local_milestone) if local_milestone else None
            labels           = ticket.get("labels", [])
            assignees        = ticket.get("assignees", [])

            issue_payload: dict = {"title": title, "body": body}
            if milestone_number:
                issue_payload["milestone"] = milestone_number
            if labels:
                issue_payload["labels"] = labels
            if assignees:
                issue_payload["assignees"] = assignees

            issue_data = rest_post(session, f"{base_url}/issues", issue_payload)
            if not issue_data:
                err(f'Skipping project link for "{title}" due to creation failure.')
                continue

            issue_number  = issue_data["number"]
            issue_node_id = issue_data["node_id"]
            issue_url     = issue_data["html_url"]

            ok(f"Created Issue #{issue_number}: {title}")
            dim(f"  {issue_url}")

        # Link issue to the Project V2
        gql_data = graphql(
            session,
            _ADD_ITEM_MUTATION,
            {"projectId": project_id, "contentId": issue_node_id},
        )

        if gql_data and gql_data.get("addProjectV2ItemById", {}).get("item", {}).get("id"):
            project_item_id = gql_data["addProjectV2ItemById"]["item"]["id"]
            ok(f"Linked to Project V2 (item: {project_item_id})")

            if fields:
                _update_item_fields(
                    session, project_id, project_item_id, ticket, fields, issue_number
                )
        else:
            warn(f"Issue #{issue_number} created but could not be linked to Project V2.")

        if idx < total:
            time.sleep(0.5)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main() -> None:
    print(f"\n{BOLD}{CYAN}{'='*60}")
    print("  GitHub Project Importer")
    print(f"{'='*60}{RESET}")

    data_dir = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("data")
    if not data_dir.is_dir():
        err(f"Data directory not found: {data_dir}")
        err("Usage: python import_project.py [path/to/data/]")
        sys.exit(1)

    info(f"Loading data from: {data_dir}/")
    project_data = load_data_dir(data_dir)

    config   = load_config()
    session  = build_session(config["GITHUB_TOKEN"])
    base_url = f"https://api.github.com/repos/{config['REPO_OWNER']}/{config['REPO_NAME']}"

    project_meta = project_data["project"]
    project_name = project_meta.get("name") or config["REPO_NAME"]

    info(f"Target repo : {config['REPO_OWNER']}/{config['REPO_NAME']}")
    info(f"Project name: {project_name}")

    milestones = project_data["milestones"] or []
    tickets    = project_data["tickets"]

    if not milestones and not tickets:
        warn("No milestones or tickets found. Nothing to do.")
        sys.exit(0)

    # Step 0: Create/reuse the Project V2
    project_id = create_project_v2(session, config["REPO_OWNER"], project_name)

    # Step 1: Setup custom fields (data-driven from fields.json)
    fields = setup_project_fields(session, project_id, project_data["fields"])

    # Step 2: Setup views (must come after fields — views reference field IDs)
    setup_project_views(session, project_id, project_data["views"], fields)

    # Step 3: Sync milestones
    milestone_map = sync_milestones(session, base_url, milestones)

    # Step 4: Ensure labels exist
    ensure_labels(session, base_url, project_data["labels"])

    # Step 5: Push issue templates to the repository
    push_issue_templates(
        session,
        config["REPO_OWNER"],
        config["REPO_NAME"],
        project_data["templates"],
    )

    # Step 6: Create issues and link to project
    create_issues(session, base_url, tickets, milestone_map, project_id, fields)

    print(f"\n{BOLD}{GREEN}{'='*60}")
    print("  Import complete!")
    print(f"{'='*60}{RESET}\n")


if __name__ == "__main__":
    main()
