<!-- ai-agent-toolkit:managed version="1.3.0" -->
<!-- Audience: Human developers. -->

# github_oauth_worker

This repository will provide the OAuth bridge for the Decap CMS integration in comic_git. It will use a GitHub App to issue least-privilege, short-lived editor tokens without giving browser clients a long-lived application secret.

## Quick Start

The local bootstrap service exposes a health endpoint only. Configuration, signed state, and access-policy foundations are implemented, but OAuth routes remain unavailable until the binding and GitHub-client tasks are complete.

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

## Docs

| Doc                                                             | Contents                                   |
|-----------------------------------------------------------------|--------------------------------------------|
| [`docs/architecture.md`](docs/architecture.md)                  | System structure and design rationale      |
| [`docs/dev_setup.md`](docs/dev_setup.md)                        | Setup, dependencies, and local development |
| [`docs/testing.md`](docs/testing.md)                            | Test strategy and conventions              |
| [`docs/infrastructure.md`](docs/infrastructure.md)              | GCP, Pulumi, environments, and secrets     |
| [`docs/contributing.md`](docs/contributing.md)                  | Branching, review, and AI workflow         |
| [`docs/features/cms-oauth/`](docs/features/cms-oauth/README.md) | CMS OAuth requirements and Decap contract  |

See [`AGENTS.md`](AGENTS.md) for agent instructions and [`docs/contributing.md`](docs/contributing.md) for the development workflow.
