<!-- ai-agent-toolkit:managed version="1.3.0" -->
<!-- Audience: Human developers. -->

# github_oauth_worker

This repository will provide the OAuth bridge for the Decap CMS integration in comic_git. It will use a GitHub App to issue least-privilege, short-lived editor tokens without giving browser clients a long-lived application secret.

## Quick Start

The service has not been bootstrapped yet. The approved implementation design and ordered work are in the ignored local planning files `specs/plan.md` and `specs/tasks.md`.

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
