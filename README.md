# gh-project-initializer

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A Python script that reads a structured `data/` directory and bootstraps a GitHub repository with a fully configured GitHub Project V2 — custom fields, views, milestones, labels, issues, and issue templates — all in one command.

## What it creates

| Feature | Details |
|---|---|
| **GitHub Project V2** | Created (or reused if it exists) |
| **Custom fields** | 8 standard fields: Component, Domain, Type, Status, Priority, Effort, Phase, Start/End Date |
| **Views** | 7 optimized views: Now, Component/Domain Overviews, Roadmap, Backlog, Bugs, Tech Debt |
| **Milestones** | Created from `milestones.json`, skipped if they already exist |
| **Labels** | Strategic labels from `labels.json` (e.g., `needs-adr`, `breaking-change`) |
| **Issues** | Created from `tickets/*.json` with rich markdown bodies and project field mapping |
| **Issue templates** | 5 YAML forms pushed to `.github/ISSUE_TEMPLATE/` (Bug, Feature, Task, Spike, Tech Debt) |

All field values are set on each issue in the project automatically. The structure follows a **filterable database** mental model, allowing you to slice and dice your work by architecture (Component) or business area (Domain).

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

`data.example/` is a "best-practice" template you can use as a starting point. Copy it to `data/` and replace the example content with your project's details:

```bash
cp -r data.example data
```

Then edit the files in `data/`:

| File | What to change |
|---|---|
| `project.json` | Your project name and description |
| `milestones.json` | Your milestones |
| `fields.json` | Adjust component/domain/phase options to match your architecture |
| `views.json` | Keep as-is to use the 7 recommended views |
| `labels.json` | Keep as-is or add project-specific labels |
| `tickets/*.json` | Replace example tickets with your own — one file per milestone |
| `templates/*.yml` | Forms that enforce structure on new issues |

`data/` is git-ignored so it stays local. `data.example/` is tracked and reflects the latest project management standards.

## Issue Hierarchy: Epics → Features → Tasks

The tool and example data encourage a three-level hierarchy:

1. **Epics:** Large initiatives (Type=Epic, Phase=MVP).
2. **Features:** Specific functionalities (Type=Feature).
3. **Tasks:** Concrete implementation items (Type=Task, Component=crate:server).

Each issue tracks its parent and contributes to the overall architectural overview in the **Component Overview** view.

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

## AI Agent Integration

This project is designed to be "AI-native". You can use AI agents to generate your entire project structure and then use this tool to deploy it.

### 1. Generate your project data
Use the [AI Project Generation Prompt](PROMPT.md) to have an AI agent (Claude, ChatGPT, etc.) generate your `data/` directory content based on your project idea.

### 2. Instructions for AI Agents
If you are an AI agent working on this project, follow these rules:
- **Data Schema:** Always refer to `data.example/` for the correct JSON/YAML schemas.
- **Incremental Updates:** To add new tickets, create a new JSON file in `data/tickets/` and run `python import_project.py`. The script will only create the new issues.
- **Field Values:** Ensure `priority`, `type`, `effort`, `component`, `domain`, and `phase` values match the options defined in `data/fields.json`.
- **Validation:** Before running the script, ensure all `depends_on` IDs exist within the current or previous ticket files.

## Notes

- **Iteration fields** cannot be created via the GitHub API — create them manually in the GitHub UI after running the script.
- The script pushes issue templates to the target repo's `.github/ISSUE_TEMPLATE/` directory via the GitHub Contents API. Your token needs `repo` write access.
- All GitHub Project V2 operations use the GraphQL API. Issue and milestone creation uses the REST API.

## License

[MIT](LICENSE)
