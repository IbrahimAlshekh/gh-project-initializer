# gh-project-initializer

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)

A Python script that reads a hierarchical project JSON file and bootstraps a GitHub repository with milestones, labels, issues, and a GitHub Project V2 — all in one command.

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

> Your token needs the `repo` and `project` scopes.

## License

[MIT](LICENSE)

## Usage

```bash
# Uses project.json in the current directory by default
python import_project.py

# Or specify a custom path
python import_project.py path/to/project.json
```
