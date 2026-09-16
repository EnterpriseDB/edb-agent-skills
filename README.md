# EDB Agent Skills

Distribution registry for compiled EDB Agent Skill artifacts. Skills published here are consumed by customer-deployed agentic systems (e.g., Claude) to interact with EDB products.

This repository is one of three in the Agent Skills pipeline:

| Repository | Role |
|---|---|
| `agent-config` | Defines project metadata and assembly parameters |
| `agent-pipeline` | Executes the CI/CD skill assembly process |
| `agent-skills` *(this repo)* | Distributes compiled skill artifacts to agentic systems |

The assembly pipeline reads configuration from `agent-config`, generates skill artifacts, and publishes them here. Versions are tagged to align with the current Hybrid Manager release.

## Directory Structure

```
agent-skills/
├── {skill_name}/
│   ├── SKILL.md
│   ├── scripts/
│   ├── references/
│   └── assets/
├── ...
└── registry.json
```

### `registry.json`

Index of all published skills. Maps skill names to their source commit SHAs and the build number they were last published with.

### `{skill_name}/`

Each skill is a self-contained directory structured for progressive disclosure — agents read `SKILL.md` first and load deeper resources only as needed.

| Path | Purpose |
|---|---|
| `SKILL.md` | Primary entry point. Contains YAML frontmatter (`name`, `description`) and core instructions for the end-agent. |
| `scripts/` | Executable code (Python, Bash) the agent can invoke to perform tasks. |
| `references/` | Long-form documentation loaded on-demand to keep `SKILL.md` lightweight. |
| `assets/` | Static resources: templates, schemas, lookup tables. |

`SKILL.md` frontmatter fields:

| Field | Constraints | Description |
|---|---|---|
| `name` | 1–64 chars, lowercase alphanumeric and hyphens | Unique skill identifier |
| `description` | Max 1024 chars | What the skill does and when an agent should trigger it |

## Versioning

Skill directories are published in place at the repo root (no per-version directory); the build number / Hybrid Manager release each skill was last published with is recorded in `registry.json` and tagged on this repository via the `Publish Skills` workflow in `agent-skills-pipeline`.
