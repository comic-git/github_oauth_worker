<!-- ai-agent-toolkit:managed version="1.0.0" -->
<!-- Audience: AI agents working on CI/CD pipelines, and developers. -->

# CI/CD

## Current State

No CI/CD configuration exists yet. The initial implementation must not add automatic deployment without explicit human approval.

## Intended Pipeline Boundary

Continuous integration should run formatting, static checks, and the offline test suite for pull requests and branches. It must not use production credentials, GitHub App secrets, or real GitHub repositories.

Deployment remains a reviewed, human-triggered Pulumi operation. A future deployment workflow must require an explicit environment selection, use environment-scoped secrets, show the Pulumi preview, and preserve a documented rollback path to the previous Cloud Run revision.

## Jobs Requiring Care

Any job that accesses GCP credentials, Pulumi state, Secret Manager, Artifact Registry, or a real GitHub App is security-sensitive. Do not modify it without human confirmation and a review of least-privilege permissions.
