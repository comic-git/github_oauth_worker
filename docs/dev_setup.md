<!-- ai-agent-toolkit:managed version="1.0.0" -->
<!-- Audience: New developers and AI agents that need to run the project locally. -->

# Dev Setup

## Current State

The application scaffold does not exist yet, so there is no runnable local command. The implementation plan will establish the exact commands and add an `.env.example`; do not infer an application package or secrets from this document before that work is complete.

## Planned Prerequisites

- Python 3.12, matching comic_git_engine's supported development target.
- A GitHub App registered for a non-production test account or organization, with its callback URL pointed at a reachable HTTPS development endpoint when testing the real authorization flow.
- Docker only when testing the production container locally.
- Google Cloud CLI and Pulumi only when provisioning a Cloud Run environment.

## Local Development Boundary

Unit and HTTP contract tests will run entirely locally with simulated GitHub responses. A real GitHub App callback requires a public HTTPS URL, so it is a manual integration test against an isolated Cloud Run sandbox after the service and infrastructure are implemented.

## Credentials

Development credentials belong in an untracked `.env` file. GitHub App client secrets, state-signing secrets, access tokens, refresh tokens, and authorization codes must never be checked in, pasted into test snapshots, or placed in logs.

## Common Setup Issue

An App callback URL must exactly match the public URL registered in GitHub. A local `http://localhost` service can be unit-tested but cannot complete the normal GitHub App browser flow without an HTTPS tunnel or sandbox deployment.
