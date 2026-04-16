#!/usr/bin/env python3
"""
import_project.py
-----------------
Reads a hierarchical project JSON file and imports it into a GitHub
Repository (Milestones, Labels, Issues) and a newly created GitHub Project V2.

Usage:
    python import_project.py [path/to/project.json]

Defaults to "project.json" in the current directory.
"""

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

    err(f"REST GET {url} → HTTP {response.status_code}")
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
  }
}
"""

_OWNER_ORG_QUERY = """
query GetOrg($login: String!) {
  organization(login: $login) {
    id
    login
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


def resolve_owner_id(session, owner_login: str) -> str:
    """
    Return the GraphQL node ID for a GitHub user or organization login.
    Tries user first; falls back to organization.
    Exits the program if neither resolves.
    """
    data = graphql(session, _OWNER_USER_QUERY, {"login": owner_login})
    if data and data.get("user"):
        node_id = data["user"]["id"]
        dim(f"Owner resolved as user: {owner_login} ({node_id})")
        return node_id

    data = graphql(session, _OWNER_ORG_QUERY, {"login": owner_login})
    if data and data.get("organization"):
        node_id = data["organization"]["id"]
        dim(f"Owner resolved as organization: {owner_login} ({node_id})")
        return node_id

    err(f'Could not resolve "{owner_login}" as a GitHub user or organization.')
    err("Check that REPO_OWNER in your .env is correct and your token has the right scopes.")
    sys.exit(1)


def create_project_v2(session, owner_login: str, title: str) -> str:
    """
    Create a new GitHub Project V2 under the given owner.
    Returns the project's GraphQL node ID (used later to link issues).
    """
    step("Step 0 — Creating GitHub Project V2")

    owner_id = resolve_owner_id(session, owner_login)

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
# Step 1 — Milestones
# ---------------------------------------------------------------------------

def sync_milestones(session, base_url: str, milestones: list) -> dict:
    """
    Create milestones from the JSON. Return a mapping of local milestone
    IDs (e.g. "M0") to GitHub milestone numbers.
    """
    step("Step 1 — Syncing Milestones")

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
# Step 2 — Labels
# ---------------------------------------------------------------------------

def ensure_labels(session, base_url: str, tickets: list) -> None:
    """
    Collect all unique labels from tickets and create any that are missing
    in the repository with a generic color.
    """
    step("Step 2 — Ensuring Labels Exist")

    # Fetch existing labels (paginate up to 300).
    existing_labels: set[str] = set()
    for page in range(1, 4):
        page_data = rest_get(session, f"{base_url}/labels", params={"per_page": 100, "page": page}) or []
        if not page_data:
            break
        existing_labels.update(lbl["name"] for lbl in page_data)

    needed: set[str] = set()
    for ticket in tickets:
        needed.update(ticket.get("labels", []))

    to_create = needed - existing_labels
    if not to_create:
        info("All labels already exist — nothing to create.")
        return

    colors = ["0075ca", "e4e669", "d73a4a", "cfd3d7", "a2eeef", "008672", "e99695"]
    for i, label in enumerate(sorted(to_create)):
        color = colors[i % len(colors)]
        data  = rest_post(session, f"{base_url}/labels", {"name": label, "color": color})
        if data:
            ok(f'Created label: "{label}"')
        else:
            warn(f'Could not create label "{label}" — it may already exist.')


# ---------------------------------------------------------------------------
# Step 3 — Issue body builder
# ---------------------------------------------------------------------------

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

    if ticket_id := ticket.get("id"):
        lines += ["---", f"*Ticket ID: `{ticket_id}`*"]

    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Step 4 — Issues
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


def create_issues(
    session,
    base_url: str,
    tickets: list,
    milestone_map: dict,
    project_id: str,
) -> None:
    """Create each ticket as a GitHub Issue and link it to the Project V2."""
    step("Step 3 — Creating Issues & Linking to Project V2")

    total = len(tickets)
    for idx, ticket in enumerate(tickets, start=1):
        title = ticket.get("title", f"Untitled Ticket {idx}")
        print(f"\n  [{idx}/{total}] {BOLD}{title}{RESET}")

        body             = build_issue_body(ticket)
        local_milestone  = ticket.get("milestone")
        milestone_number = milestone_map.get(local_milestone) if local_milestone else None
        labels           = ticket.get("labels", [])

        issue_payload: dict = {"title": title, "body": body}
        if milestone_number:
            issue_payload["milestone"] = milestone_number
        if labels:
            issue_payload["labels"] = labels

        issue_data = rest_post(session, f"{base_url}/issues", issue_payload)
        if not issue_data:
            err(f'Skipping project link for "{title}" due to creation failure.')
            continue

        issue_number  = issue_data["number"]
        issue_node_id = issue_data["node_id"]
        issue_url     = issue_data["html_url"]

        ok(f"Created Issue #{issue_number}: {title}")
        dim(f"  {issue_url}")

        gql_data = graphql(
            session,
            _ADD_ITEM_MUTATION,
            {"projectId": project_id, "contentId": issue_node_id},
        )

        if gql_data and gql_data.get("addProjectV2ItemById", {}).get("item", {}).get("id"):
            project_item_id = gql_data["addProjectV2ItemById"]["item"]["id"]
            ok(f"Linked to Project V2 (item: {project_item_id})")
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

    json_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("project.json")
    if not json_path.exists():
        err(f"JSON file not found: {json_path}")
        sys.exit(1)

    info(f"Reading: {json_path}")

    with json_path.open(encoding="utf-8") as fh:
        data = json.load(fh)

    config   = load_config()
    session  = build_session(config["GITHUB_TOKEN"])
    base_url = f"https://api.github.com/repos/{config['REPO_OWNER']}/{config['REPO_NAME']}"

    project_meta = data.get("project", {})
    project_name = project_meta.get("name") or config["REPO_NAME"]

    info(f"Target repo : {config['REPO_OWNER']}/{config['REPO_NAME']}")
    info(f"Project name: {project_name}")

    milestones = data.get("milestones", [])
    tickets    = data.get("tickets", [])

    if not milestones and not tickets:
        warn("No milestones or tickets found in JSON. Nothing to do.")
        sys.exit(0)

    # Step 0: create the Project V2 from scratch.
    project_id = create_project_v2(session, config["REPO_OWNER"], project_name)

    milestone_map = sync_milestones(session, base_url, milestones)
    ensure_labels(session, base_url, tickets)
    create_issues(session, base_url, tickets, milestone_map, project_id)

    print(f"\n{BOLD}{GREEN}{'='*60}")
    print("  Import complete!")
    print(f"{'='*60}{RESET}\n")


if __name__ == "__main__":
    main()
