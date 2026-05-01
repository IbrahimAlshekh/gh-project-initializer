# AI Project Generation Prompt

Copy and paste the prompt below into an AI agent (like ChatGPT, Claude, or Junie) to generate the structured data files required by this project.

---

## The Prompt

I want you to act as a Senior Project Manager and Software Architect. I am starting a new project called "**[INSERT PROJECT NAME]**".

**Project Description:**
[INSERT SHORT DESCRIPTION OF YOUR PROJECT]

**Core Tech Stack:**
[INSERT TECH STACK, e.g., Next.js, FastAPI, PostgreSQL]

**Your Task:**
Generate a set of structured JSON files that define the initial project roadmap, milestones, and technical tickets, following the schema required by my `gh-project-initializer` tool.

### 1. Milestones (`milestones.json`)
Create 3-5 milestones (e.g., M0: Foundation, M1: Core Features, M2: Polish & Launch).

### 2. Tickets (`tickets/*.json`)
For each milestone, generate a JSON file (e.g., `00-foundation.json`, `01-core.json`). 
Each ticket MUST follow this schema:
```json
{
  "id": "T-001",
  "title": "Short descriptive title",
  "priority": "P0/P1/P2/P3",
  "type": "Epic/Feature/Task/Bug/Tech Debt/Spike/Docs",
  "effort": "XS/S/M/L/XL",
  "component": "[Relevant Component]",
  "domain": "[Relevant Domain]",
  "phase": "MVP/v1.0/v1.1/v2.0/Future",
  "description": "A 1-2 sentence overview of the goal.",
  "tasks": ["Specific task 1", "Specific task 2"],
  "acceptance_criteria": ["Criteria 1", "Criteria 2"],
  "depends_on": ["T-00X"], 
  "start_date": "YYYY-MM-DD",
  "end_date": "YYYY-MM-DD"
}
```

### 3. Project Metadata (`project.json`)
```json
{
  "name": "[Project Name]",
  "description": "[Project Description]",
  "tech_stack": ["[Tech 1]", "[Tech 2]"]
}
```

### Important Guidelines:
- **Issue Hierarchy:** Use a three-level hierarchy where possible:
  - **Epics:** Large initiatives (e.g., "Reseller System").
  - **Features:** Specific functionalities within an Epic (e.g., "Credit Purchase Flow").
  - **Tasks:** Concrete implementation steps (e.g., "Stripe checkout endpoint").
- **Component & Domain:** Be consistent with naming. Examples for components: `crate:core`, `crate:server`, `crate:app`, `infra`. Examples for domains: `auth`, `billing`, `sync`, `player`.
- **Logical Dependencies:** Ensure `depends_on` correctly references IDs of earlier tickets.
- **Realistic Estimates:** Set `start_date` and `end_date` based on a logical progression starting from today.
- **Technical Depth:** Include specific technical tasks relevant to the tech stack.
- **Fields Alignment:** Use the standard options for Priority, Type, and Effort.

### How to use Example Data:
- I have provided `data.example/` in the repository which contains a best-practice configuration for fields, views, and templates.
- **DO NOT** just copy the example data blindly. Use it as a reference for the JSON structure.
- Generate your data based on the specific needs of "[INSERT PROJECT NAME]" but try to follow the "Component Overview" and "Domain Overview" philosophy seen in the examples.

Please provide the content for these files in a way that I can easily save them into the `data/` directory.
