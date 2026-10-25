<!-- ai-agent-toolkit:managed version="1.3.0" -->
<!-- Audience: Human developers. -->

# github_oauth_worker

This repository will provide the OAuth bridge for the Decap CMS integration in comic_git. It will use a GitHub App to issue least-privilege, short-lived editor tokens without giving browser clients a long-lived application secret.

## Quick Start

The worker serves GitHub App OAuth for Decap and guides repository owners through CMS setup. Website authorization is defined by `.comic_git/cms_access.toml` on the repository's current default branch; the worker has no runtime database dependency. Start with the [infrastructure setup guide](infra/README.md) when deploying a worker.

Creators should start at the deployed worker's `/setup` URL before installing its GitHub App. The worker guides them to GitHub to grant repository access when needed, then returns them to branch selection and migration review. See [CMS enablement](docs/features/cms-oauth/cms-enablement.md) for the full flow. Installing the App directly first is also supported.

```powershell
py -3.14 -m venv .venv
.\.venv\Scripts\python.exe -m pip install --upgrade pip
.\.venv\Scripts\python.exe -m pip install -e ".[dev]"
.\.venv\Scripts\python.exe -m pytest
.\.venv\Scripts\python.exe -m ruff check .
.\.venv\Scripts\python.exe -m uvicorn github_oauth_worker.app:app --reload
```

The health endpoint is available at `http://127.0.0.1:8000/health`.

Copy `.env.example` to an untracked `.env` before later configuration work adds operational settings.

Self-hosted deployments must keep the default `ACCESS_POLICY=github_login_whitelist` and define `GITHUB_LOGIN_WHITELIST`. The `public` policy exists only for the comic_git-operated shared worker.

See [`docs/dev_setup.md`](docs/dev_setup.md) for planned prerequisites and the local-development boundary.
Before creating or deploying a hosted worker, follow the owner setup in [`infra/README.md`](infra/README.md).

## Docs

| Doc                                                             | Contents                                   |
|-----------------------------------------------------------------|--------------------------------------------|
| [`docs/architecture.md`](docs/architecture.md)                  | System structure and design rationale      |
| [`docs/dev_setup.md`](docs/dev_setup.md)                        | Setup, dependencies, and local development |
| [`docs/testing.md`](docs/testing.md)                            | Test strategy and conventions              |
| [`docs/infrastructure.md`](docs/infrastructure.md)              | GCP, Pulumi, environments, and secrets     |
| [`docs/cicd.md`](docs/cicd.md)                            | CI previews and approval-gated deployments |
| [`docs/contributing.md`](docs/contributing.md)                  | Branching, review, and AI workflow         |
| [`docs/features/cms-oauth/`](docs/features/cms-oauth/README.md) | CMS OAuth requirements and Decap contract  |
| [`docs/backlog.md`](docs/backlog.md)                            | Deferred work and pre-release checks       |

See [`AGENTS.md`](AGENTS.md) for agent instructions and [`docs/contributing.md`](docs/contributing.md) for the development workflow.
