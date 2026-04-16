#!/usr/bin/env python3
"""
import_project.py
-----------------
Reads a hierarchical project JSON file and imports it into a GitHub
Repository (Milestones, Labels, Issues) and a GitHub Project V2 via GraphQL.

Usage:
    python import_project.py [path/to/project.json]

Defaults to "project.json" in the current directory.

See the guide at the bottom of this file for how to obtain PROJECT_ID.
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


def ok(msg: str)    -> None: print(f"  {GREEN}✅ {msg}{RESET}")
def warn(msg: str)  -> None: print(f"  {YELLOW}⚠️  {msg}{RESET}")
def err(msg: str)   -> None: print(f"  {RED}❌ {msg}{RESET}")
def info(msg: str)  -> None: print(f"  {CYAN}ℹ️  {msg}{RESET}")
def step(msg: str)  -> None: print(f"\n{BOLD}{msg}{RESET}")
def dim(msg: str)   -> None: print(f"  {DIM}{msg}{RESET}")


# ---------------------------------------------------------------------------
# Environment & config
# ---------------------------------------------------------------------------

def load_config() -> dict:
    """Load and validate required environment variables."""
    load_dotenv()

    required = ["GITHUB_TOKEN", "REPO_OWNER", "REPO_NAME", "PROJECT_ID"]
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
    Raises on any other non-2xx status after exhausting retries.
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

    # If we exhausted retries, return the last response and let callers decide.
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

    # Determine which labels we need.
    needed: set[str] = set()
    for ticket in tickets:
        needed.update(ticket.get("labels", []))

    to_create = needed - existing_labels
    if not to_create:
        info("All labels already exist — nothing to create.")
        return

    # A small palette so auto-created labels aren't all the same colour.
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

    # Description
    if description := ticket.get("description", "").strip():
        lines += ["## Description", "", description, ""]

    # Tasks checklist
    if tasks := ticket.get("tasks", []):
        lines += ["## Tasks", ""]
        lines += [f"- [ ] {t}" for t in tasks]
        lines.append("")

    # Acceptance criteria checklist
    if criteria := ticket.get("acceptance_criteria", []):
        lines += ["## Acceptance Criteria", ""]
        lines += [f"- [ ] {c}" for c in criteria]
        lines.append("")

    # Learning goals bulleted list
    if goals := ticket.get("learning_goals", []):
        lines += ["## Learning Goals", ""]
        lines += [f"- {g}" for g in goals]
        lines.append("")

    # Footer
    if ticket_id := ticket.get("id"):
        lines += [f"---", f"*Ticket ID: `{ticket_id}`*"]

    return "\n".join(lines).strip()


# ---------------------------------------------------------------------------
# Step 4 — Issues
# ---------------------------------------------------------------------------

ADD_ITEM_MUTATION = """
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

        # Build the request payload.
        body             = build_issue_body(ticket)
        local_milestone  = ticket.get("milestone")
        milestone_number = milestone_map.get(local_milestone) if local_milestone else None
        labels           = ticket.get("labels", [])

        issue_payload: dict = {"title": title, "body": body}
        if milestone_number:
            issue_payload["milestone"] = milestone_number
        if labels:
            issue_payload["labels"] = labels

        # Create the issue via REST.
        issue_data = rest_post(session, f"{base_url}/issues", issue_payload)
        if not issue_data:
            err(f'  Skipping project link for "{title}" due to creation failure.')
            continue

        issue_number  = issue_data["number"]
        issue_node_id = issue_data["node_id"]
        issue_url     = issue_data["html_url"]

        ok(f"Created Issue #{issue_number}: {title}")
        dim(f"  {issue_url}")

        # Link to Project V2 via GraphQL.
        gql_data = graphql(
            session,
            ADD_ITEM_MUTATION,
            {"projectId": project_id, "contentId": issue_node_id},
        )

        if gql_data and gql_data.get("addProjectV2ItemById", {}).get("item", {}).get("id"):
            project_item_id = gql_data["addProjectV2ItemById"]["item"]["id"]
            ok(f"Linked to Project V2 (item: {project_item_id})")
        else:
            warn(f"Issue #{issue_number} created but could not be linked to Project V2.")

        # Small polite pause between issues to avoid secondary rate limits.
        if idx < total:
            time.sleep(0.5)


# ---------------------------------------------------------------------------
# Main entry point
# ---------------------------------------------------------------------------

def main() -> None:
    print(f"\n{BOLD}{CYAN}{'='*60}")
    print("  GitHub Project Importer")
    print(f"{'='*60}{RESET}")

    # Locate the JSON file (first CLI arg or default).
    json_path = Path(sys.argv[1]) if len(sys.argv) > 1 else Path("project.json")
    if not json_path.exists():
        err(f"JSON file not found: {json_path}")
        sys.exit(1)

    info(f"Reading: {json_path}")

    with json_path.open(encoding="utf-8") as fh:
        data = json.load(fh)

    config     = load_config()
    session    = build_session(config["GITHUB_TOKEN"])
    base_url   = f"https://api.github.com/repos/{config['REPO_OWNER']}/{config['REPO_NAME']}"
    project_id = config["PROJECT_ID"]

    info(f"Target repo  : {config['REPO_OWNER']}/{config['REPO_NAME']}")
    info(f"Project V2 ID: {project_id}")

    milestones = data.get("milestones", [])
    tickets    = data.get("tickets", [])

    if not milestones and not tickets:
        warn("No milestones or tickets found in JSON. Nothing to do.")
        sys.exit(0)

    milestone_map = sync_milestones(session, base_url, milestones)
    ensure_labels(session, base_url, tickets)
    create_issues(session, base_url, tickets, milestone_map, project_id)

    print(f"\n{BOLD}{GREEN}{'='*60}")
    print("  Import complete!")
    print(f"{'='*60}{RESET}\n")


if __name__ == "__main__":
    main()


# =============================================================================
# HOW TO FIND YOUR GITHUB PROJECT V2 NODE ID
# =============================================================================
#
# The GitHub UI shows a simple project NUMBER in the URL, e.g.:
#   https://github.com/orgs/my-org/projects/5   ← "5" is the number
#
# The GraphQL API needs the *internal Node ID*, which looks like:
#   PVT_kwDOBxxxxxxxxxxxxxxx
#
# There are two easy ways to find it:
#
# ── Option A: GitHub CLI (recommended) ──────────────────────────────────────
#
#   For a USER-owned project:
#     gh project list --owner YOUR_USERNAME --format json \
#       | python3 -c "import json,sys; [print(p['id'], p['number'], p['title']) for p in json.load(sys.stdin)['projects']]"
#
#   For an ORG-owned project:
#     gh project list --owner YOUR_ORG --format json \
#       | python3 -c "import json,sys; [print(p['id'], p['number'], p['title']) for p in json.load(sys.stdin)['projects']]"
#
#   The 'id' column in the output IS the Node ID. Copy it into PROJECT_ID in .env.
#
# ── Option B: GraphQL query ─────────────────────────────────────────────────
#
#   Run the query below (replace <YOUR_LOGIN> and <PROJECT_NUMBER>):
#
#   curl -s -X POST https://api.github.com/graphql \
#     -H "Authorization: Bearer YOUR_TOKEN" \
#     -H "Content-Type: application/json" \
#     -d '{
#       "query": "{ user(login: \"<YOUR_LOGIN>\") { projectV2(number: <PROJECT_NUMBER>) { id title } } }"
#     }' | python3 -m json.tool
#
#   For an org, replace `user` with `organization`:
#
#   curl -s -X POST https://api.github.com/graphql \
#     -H "Authorization: Bearer YOUR_TOKEN" \
#     -H "Content-Type: application/json" \
#     -d '{
#       "query": "{ organization(login: \"<YOUR_ORG>\") { projectV2(number: <PROJECT_NUMBER>) { id title } } }"
#     }' | python3 -m json.tool
#
#   The response will contain:
#     "id": "PVT_kwDOBxxxxxxxxxxxxxxx"
#
#   Copy that value into PROJECT_ID in your .env file.
# =============================================================================
