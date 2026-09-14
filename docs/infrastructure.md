<!-- ai-agent-toolkit:managed version="1.0.0" -->
<!-- Audience: AI agents working on infrastructure or deployment, and developers. -->

# Infrastructure

## Environments

| Environment | Purpose | Access |
|---|---|---|
| Local | Fast feedback with simulated GitHub responses | Developer machine; no real OAuth callback. |
| Sandbox | Manual real-GitHub-App and Decap integration verification | Separate GCP project or Pulumi stack and a test GitHub App. |
| Production | Shared OAuth service for comic_git sites | Public Cloud Run URL; operator controlled. |

## Planned Cloud Services

| Service | Purpose |
|---|---|
| Cloud Run | Public stateless FastAPI service with scale-to-zero. |
| Artifact Registry | Stores the service container image. |
| Secret Manager | Stores GitHub App and state-signing secrets. |
| Service Account and IAM | Gives only the Cloud Run runtime access to required secrets. |
| Cloud Billing budget | Alerts the operator before unexpected spending. |

## Infrastructure as Code

Pulumi Python will live under `infra/` after implementation. It will create GCP resources and bind runtime secrets, but it will not create a GitHub App or import generated GitHub credentials into source control. Each environment is a separate Pulumi stack with explicit project, region, public worker URL, CMS-origin allowlist, and non-secret access-policy configuration. The initial Cloud Run revision runs in explicit bootstrap mode, exposing only health status until App credentials are configured.

Applying Pulumi changes or deploying Cloud Run requires explicit human confirmation. Before any apply, inspect the Pulumi preview and verify the target project and stack.

## Networking

Cloud Run must allow unauthenticated ingress because Decap opens the service from an editor's browser. The service exposes only its authentication and health endpoints. It needs no VPC, database, queue, or inbound GitHub webhook for the MVP.

## Secrets Management

The deployer generates the GitHub App client secret in GitHub's settings and supplies it, along with a state-signing secret, through Pulumi's secret configuration or the configured Secret Manager workflow. Cloud Run reads them through its dedicated service account. Never place secrets in a registration checklist, `.env.example`, command history, source code, Pulumi non-secret config, logs, test fixtures, or browser error messages.
