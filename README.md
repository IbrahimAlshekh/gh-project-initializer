# gh-project-initializer

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A Python script that reads a structured `data/` directory and bootstraps a GitHub repository with a fully configured GitHub Project V2 — custom fields, views, milestones, labels, issues, and issue templates — all in one command.

## What it creates

| Feature | Details |
|---|---|
| **GitHub Project V2** | Created (or reused if it exists) |
| **Custom fields** | Up to 8 fields: Component, Domain, Type, Priority, Effort, Phase, Start Date, End Date |
| **Views** | 7 views: Now (board), Component Overview, Domain Overview, Roadmap, Backlog, Bugs, Tech Debt |
| **Milestones** | Created from `milestones.json`, skipped if they already exist |
| **Labels** | Strategic labels from `labels.json` (not duplicating field values) |
| **Issues** | Created from `tickets/*.json` with rich markdown bodies, linked to the project |
| **Issue templates** | 5 YAML forms pushed to `.github/ISSUE_TEMPLATE/` in the target repo |

All field values (Priority, Effort, Type, etc.) are set on each issue in the project automatically.

## Installation

**Prerequisites:** Python 3.8+

```bash
# Clone the repo
git clone https://github.com/ibrahimalshekh/gh-project-initializer.git
cd gh-project-initializer

# Create and activate a virtual environment
python3 -m venv .venv
source .venv/bin/activate  # On Windows: .venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt
```

## Configuration

Copy `.env.example` to `.env` and fill in your values:

```
GITHUB_TOKEN=your_personal_access_token
REPO_OWNER=your_github_username_or_org
REPO_NAME=your_repository_name
```

Your token needs the `repo` and `project` scopes.

## Setup for your project

`data.example/` is a working example you can use as a starting point. Copy it to `data/` and replace the example content with your project's details:

```bash
cp -r data.example data
```

Then edit the files in `data/`:

| File | What to change |
|---|---|
| `project.json` | Your project name and description |
| `milestones.json` | Your milestones |
| `fields.json` | Adjust component/domain/phase options to match your architecture |
| `views.json` | Keep as-is or add/remove views |
| `labels.json` | Keep as-is or add project-specific labels |
| `tickets/*.json` | Replace example tickets with your own — one file per milestone |
| `templates/*.yml` | Keep as-is or customize the issue form fields |

`data/` is git-ignored so it stays local. `data.example/` is tracked and reusable across projects.

## Data directory layout

```
data/
  project.json        Project metadata (name, description, tech_stack)
  fields.json         Custom field definitions (SINGLE_SELECT, DATE, NUMBER)
  views.json          View definitions (layout, filter, group_by)
  labels.json         Label definitions (name, color, description)
  milestones.json     Milestone list
  tickets/            One JSON file per milestone group
    00-foundation.json
    01-user-roles.json
    ...
  templates/          Issue template YAML forms
    bug.yml
    feature.yml
    task.yml
    tech-debt.yml
    spike.yml
```

All files except `project.json` and `milestones.json` are optional — the script skips any missing file or directory gracefully.

## Ticket schema

Each ticket file in `tickets/` has this structure:

```json
{
  "milestone": "M0",
  "tickets": [
    {
      "id": "T-001",
      "title": "Ticket title",
      "priority": "High",
      "type": "Feature",
      "effort": "M",
      "component": "Auth",
      "domain": "Backend",
      "phase": "Core Features",
      "labels": [],
      "depends_on": [],
      "description": "What to build",
      "tasks": ["Step 1", "Step 2"],
      "acceptance_criteria": ["Criterion 1"],
      "learning_goals": ["Goal 1"],
      "start_date": "2026-05-01",
      "end_date": "2026-05-05"
    }
  ]
}
```

Field values (`priority`, `type`, `effort`, `component`, `domain`, `phase`) must match option names defined in `fields.json`. The script does case-insensitive matching.

## Usage

```bash
# Uses data/ in the current directory by default
python import_project.py

# Or specify a custom data directory
python import_project.py path/to/data/
```

The script is fully idempotent — running it again on an existing repo skips anything that already exists (project, milestones, labels, issues, templates).

## Notes

- **Iteration fields** cannot be created via the GitHub API — create them manually in the GitHub UI after running the script.
- The script pushes issue templates to the target repo's `.github/ISSUE_TEMPLATE/` directory via the GitHub Contents API. Your token needs `repo` write access.
- All GitHub Project V2 operations use the GraphQL API. Issue and milestone creation uses the REST API.

## License

[MIT](LICENSE)
