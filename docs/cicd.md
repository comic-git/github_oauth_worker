<!-- ai-agent-toolkit:managed version="1.0.0" -->
<!-- Audience: AI agents working on CI/CD pipelines, and developers. -->

# CI/CD

## Pipeline

The initial pipeline has three required jobs for every commit and pull request:

1. `test` creates a venv, installs locked development dependencies, and runs formatting, static checks, and the offline test suite.
2. `pulumi-preview-test` depends on `test` and runs a detailed Pulumi preview against the test stack.
3. `pulumi-apply-test` depends on `pulumi-preview-test` and applies the reviewed test-stack change on commits to the default branch.

Production uses the same ordered preview then apply jobs, but production apply is manually dispatched. This repository currently has one developer, so the workflow does not require manually copying a preview identifier into the apply job. Preview logs remain the required review surface before applying infrastructure.

## Credentials and Boundaries

Test and production use separate GitHub Apps, Cloud Run services, Firestore databases, runtime service accounts, and Secret Manager secrets. CI test credentials may access only the test environment. Production credentials are available only to the manually dispatched production workflow.

CI must never print Pulumi secret values, GitHub App credentials, user tokens, or Firestore binding records. Infrastructure jobs require particular review because they can alter public endpoints, identity permissions, or persistent data.

## Labels

Every label-capable GCP resource managed for this product uses `product=github_oauth_worker`, `service=github-oauth-worker`, and its `environment` label. This supports cost and inventory filtering across the shared GCP project.
