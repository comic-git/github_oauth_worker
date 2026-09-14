<!-- ai-agent-toolkit:managed version="1.3.0" -->
<!-- This file is the AI entry point for this repo. It orients AI agents operating here and sets
     universal behavioral rules that apply regardless of task or workflow.
     Workflow-specific instructions belong in agent definition files, not here.
     Keep this file short — focused context produces better AI output than comprehensive context. -->

# AGENTS.md

## Project Overview

github_oauth_worker is a self-hostable OAuth bridge for comic_git Decap CMS sites. It authenticates editors through a GitHub App, applies an optional GitHub-login whitelist, and returns the short-lived user token that Decap uses to edit the configured repository.

## Behavioral Guardrails

The following actions require explicit human confirmation before proceeding, regardless of task or workflow:

- **Never push to remote** without human confirmation
- **Never merge or close MRs/PRs** without human confirmation
- **Never run destructive operations** (drop tables, delete branches, `rm -rf`, truncate data) without human confirmation
- **Never modify CI/CD configuration** without human confirmation
- **Never deploy to any environment** without human confirmation

On Claude Code, the ai-agent-toolkit hooks (`guard-remote-git`, `guard-destructive`, `guard-cicd-config`, `guard-deploy`) enforce these guardrails deterministically by forcing a confirmation prompt on the matching action. On other platforms, this section is the enforcement.

## Writing Conventions

No repository-specific technical-writer guide is installed yet. Follow the writing rules below for repo documentation, MR/PR descriptions, and other project artifacts:

- Content the user deleted stays deleted: the file on disk is the baseline, never an earlier draft. Recompute derived values downward instead of restoring cuts
- The writing process is never the subject: no changelog of the editing session in any artifact — no revision narratives or "previously/now" framing in docs and tickets, no metrics about the document's own updates, no code comments explaining what changed. The project's evolution may be documented; the artifact's evolution is not
- Never hard-wrap prose: one paragraph or list item is one physical line
- MR/PR descriptions follow the `git-engineer` agent (fenced ```markdown block, short, technical-writer style)

## Key Docs

Humans and agents alike: read whichever doc covers the task at hand before acting (for example, a question about branching is answered by [`docs/contributing.md`](docs/contributing.md)).

| Doc                                                      | Contents                                                      |
|----------------------------------------------------------|---------------------------------------------------------------|
| [`docs/architecture.md`](docs/architecture.md)           | System structure, components, design rationale                |
| [`docs/dev_setup.md`](docs/dev_setup.md)                 | First-time setup: dependencies, env vars, local startup       |
| [`docs/testing.md`](docs/testing.md)                     | Test frameworks, how to run and write tests                   |
| [`docs/debugging.md`](docs/debugging.md)                 | Common failures, log reading, useful commands                 |
| [`docs/coding-principles.md`](docs/coding-principles.md) | How to write code that fits this repo                         |
| [`docs/contributing.md`](docs/contributing.md)           | Branching, MR process, available AI agents                    |
| [`docs/documentation.md`](docs/documentation.md)         | Docs structure, philosophy, and where new content belongs     |
| [`docs/infrastructure.md`](docs/infrastructure.md)       | GCP, Pulumi, environments, and secrets                        |
| [`docs/features/cms-oauth/`](docs/features/cms-oauth/)   | CMS OAuth requirements and Decap contract                     |
