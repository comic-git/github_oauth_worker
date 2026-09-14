<!-- ai-agent-toolkit:managed version="1.1.0" -->
<!-- Audience: AI agents and human developers. -->

# Architecture

## Overview

oauth_worker is a self-hostable OAuth bridge for comic_git Decap CMS sites. It issues GitHub App user tokens only after it verifies a binding between the browser's actual CMS origin and one repository selected during GitHub App installation.

The shared service can accept self-service enrollment for any eligible comic_git owner. Independently hosted services default to a GitHub-login allowlist. Both modes use the same binding and repository-verification controls, so `public` policy never means that an arbitrary browser origin can receive an editor token.

## Components

| Component       | Responsibility                                                                                        |
|-----------------|-------------------------------------------------------------------------------------------------------|
| FastAPI service | Implements Decap, enrollment, setup, migration, and health HTTP flows.                                |
| Binding store   | Firestore records verified repository-to-origin bindings and direct origin lookup records.            |
| Access policy   | Allows or denies a GitHub login at enrollment, authorization, and refresh.                            |
| GitHub App      | Defines user-token permissions and lets owners select exactly which repositories the service may use. |
| Cloud Run       | Runs the public stateless service with scale-to-zero.                                                 |
| Secret Manager  | Holds the GitHub client secret and state-signing secret.                                              |
| Pulumi          | Provisions GCP resources and their least-privilege bindings.                                          |

## Authorization Flow

1. Decap opens the worker popup and performs its non-secret handshake.
2. The worker captures the opener's actual origin from `MessageEvent.origin`, then resolves the direct Firestore binding for that canonical origin.
3. An unknown origin enters self-service enrollment. An inactive binding produces a safe error. An active binding continues to GitHub authorization.
4. The worker evaluates policy, exchanges the GitHub code with the binding's repository ID, and independently verifies that the returned user token can access the bound repository.
5. The callback posts Decap's result only to that exact origin. The worker retains no editor token or refresh token.
6. Refresh repeats policy and repository-access verification before returning a refreshed token.

## Enrollment and Migration

Enrollment verifies an App installation and user-selected repository before creating a binding. The GitHub setup URL is used after installation and installation updates, but its supplied installation ID is never trusted on its own; the worker authenticates the user and verifies the installation/repository through GitHub before writing data.

Repository IDs, not owner/name strings, are binding identities. Renames only refresh display metadata. A transfer or changed App repository selection causes verification to fail safely and directs the new administrator into recovery enrollment. A custom-domain migration starts at an existing bound origin, requires the new origin's actual popup handshake to complete, and leaves the old origin active for a configured grace period.

## External Boundaries

The endpoint and security contract is in [CMS OAuth worker contract](features/cms-oauth/worker-contract.md). comic_git_engine remains the authority for CMS TOML/content compatibility; this worker does not parse comic data or act as a GitHub proxy.

## Design Constraints

- The worker must be publicly reachable for browser popups, but only registered exact origins receive callback tokens.
- The worker uses the GitHub App client ID and client secret for user authorization. It does not hold an App private key or issue installation access tokens in the MVP.
- Firestore stores binding metadata, not GitHub credentials or user sessions.
- The normal authentication hot path is a direct origin-record lookup, not a collection query.
