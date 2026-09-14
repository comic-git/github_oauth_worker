<!-- ai-agent-toolkit:managed version="1.1.0" -->
<!-- Audience: New developers and AI agents that need to run the project locally. -->

# Dev Setup

## Current State

The application scaffold does not exist yet, so there is no runnable local command. The implementation will add the exact venv commands, locked dependency installation, and `.env.example`; do not infer an application package or secrets from this document before that work is complete.

## Planned Prerequisites

- The latest stable Python version supported by Cloud Run, CI, and dependencies; currently Python 3.14.
- A local Python venv for all normal development, static checks, and offline tests.
- A dedicated sandbox GitHub App and public HTTPS test worker only for real browser authorization/enrollment tests.
- Google Cloud CLI and Pulumi only when provisioning or inspecting cloud infrastructure.

Docker is not required for normal local development. The deployment path may use Cloud Run source builds or a CI-built container image, but the local venv is the authoritative fast-feedback environment.

## Environment Separation

The test and production workers have different public URLs, GitHub Apps, client credentials, service accounts, and Firestore database IDs. A comic_git CMS configuration selects the environment by using that worker's URL. Enrollment never asks a user to choose test or production.

## Credentials

Local credentials belong in an untracked `.env` file. GitHub App client secrets, state-signing secrets, access tokens, refresh tokens, and authorization codes must never be checked in, pasted into test snapshots, or placed in logs.

## Common Setup Issue

An App callback or setup URL must exactly match the public worker URL registered in GitHub. A local `http://localhost` service can run all offline tests but cannot complete the normal browser flow without a public HTTPS sandbox endpoint.
