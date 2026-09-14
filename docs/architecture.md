<!-- ai-agent-toolkit:managed version="1.1.0" -->
<!-- Audience: AI agents and human developers. -->

# Architecture

## Overview

oauth_worker is a stateless HTTP service that implements Decap CMS's GitHub authentication handshake for comic_git. It uses GitHub App user access tokens so repository access is limited by the App installation, the App permissions, and the editor's own GitHub permissions.

## Components

| Component        | Responsibility                                                                                                                                 |
|------------------|------------------------------------------------------------------------------------------------------------------------------------------------|
| FastAPI service  | Validates Decap requests, manages OAuth state, exchanges GitHub authorization codes, and returns the Decap popup result.                       |
| Access policy    | Permits or denies an authenticated GitHub login. The MVP policy is a deployer-controlled allowlist.                                            |
| GitHub App       | Defines the permissions and repositories available to editor tokens. It is registered through a versioned manifest and installed by its owner. |
| Google Cloud Run | Runs the public, stateless worker with scale-to-zero.                                                                                          |
| Secret Manager   | Holds the GitHub App client secret and the state-signing secret.                                                                               |
| Pulumi           | Provisions and configures the GCP resources. It does not register or install the GitHub App.                                                   |

## Request Flow

1. Decap opens `GET /auth?provider=github&site_id=<hostname>` in a popup.
2. The worker validates the provider and site identifier, creates short-lived signed state, and redirects the popup to GitHub's App authorization page.
3. GitHub redirects to `GET /callback` with an authorization code and state.
4. The worker validates state, exchanges the code for a GitHub App user token, checks the GitHub login against the access policy, and sends Decap's `authorization:github:success` message to the opener.
5. Decap uses the returned token directly with GitHub's API. The worker does not retain it.
6. When Decap refreshes a token, it calls `POST /auth/refresh`; the worker repeats the policy check before returning the refreshed token.

## External Contracts

The public Decap endpoint and callback message contract are specified in [CMS OAuth worker contract](features/cms-oauth/worker-contract.md). The comic_git engine remains the authority for content/TOML compatibility; this service only authenticates editor requests.

## Key Dependencies

| Dependency            | Purpose                                                 | Notes                                                                                                   |
|-----------------------|---------------------------------------------------------|---------------------------------------------------------------------------------------------------------|
| GitHub App API        | User authorization-code exchange and token refresh      | Use short-lived user access tokens with no OAuth scopes.                                                |
| Decap CMS             | Browser popup and `postMessage` authentication protocol | Decap validates that popup messages originate from the configured worker URL.                           |
| Google Cloud Run      | Public HTTP execution                                   | No database, VPC, or queue is required for the MVP.                                                     |
| Google Secret Manager | Runtime credentials                                     | Secrets must never enter source control, logs, or browser responses beyond Decap's required user token. |

## Design Constraints

- The service is public because Decap must open it in an editor's browser, but it has no unauthenticated repository-writing endpoint.
- A self-hosting administrator owns and installs their own GitHub App. A shared hosted service owns one App and users install it in their permitted repositories.
- A GitHub App registration is deliberately not fully managed by Pulumi: the owner must consent to the App's permissions and handle the generated client secret. The deployment process uses a checked-in manifest plus Secret Manager rather than an untracked set of browser settings.
- The initial state mechanism is cookie-backed and short-lived, so no database is necessary for CSRF protection or callback correlation.
