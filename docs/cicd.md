<!-- ai-agent-toolkit:managed version="1.1.0" -->
<!-- Audience: AI agents working on CI/CD pipelines, and developers. -->

# CI/CD

## Pipeline

The initial pipeline has three ordered jobs:

1. `test` creates a venv, installs locked development dependencies, and runs formatting, static checks, and the offline test suite.
2. `pulumi-preview-test` depends on `test`, builds and pushes a fresh immutable worker image, then runs a detailed Pulumi preview against the test stack using that image digest.
3. `pulumi-apply-test` depends on `pulumi-preview-test` and applies the reviewed test-stack change using the produced image digest.

`test` runs for every commit and pull request. During MVP development, the test-stack preview and apply jobs run after a successful test job on commits to every branch. After MVP, those infrastructure jobs run only for non-`master` branches, leaving `master` to the production path.

Production preview runs only after successful tests on `master`, and likewise builds and pushes a fresh immutable image before previewing against its digest. Production apply is manually dispatched, restricted to `master`, and depends on the production preview. This repository currently has one developer, so the workflow does not require manually copying a preview identifier into the apply job. Preview logs remain the required review surface before applying infrastructure.

Every workflow that can create a Cloud Run revision must publish a fresh image before its preview. This makes Artifact Registry's 30-day retention safe even when an infrastructure-only change creates a new revision: Pulumi never needs a previously cleaned-up image digest.

## Credentials and Boundaries

Test and production use separate GitHub Apps, Cloud Run services, Firestore databases, runtime service accounts, and Secret Manager secrets. CI authenticates through GitHub Actions Workload Identity Federation rather than a stored GCP service-account key. The owner creates the GCS Pulumi-state bucket manually, then bootstraps federation locally through the separate `infra/bootstrap/` Pulumi program; normal CI cannot change its own identity path. Bootstrap outputs the non-secret provider and service-account identifiers stored as GitHub repository variables. CI test credentials may access only the test environment. Production credentials are available only to the manually dispatched production workflow on `master`.

CI must never print Pulumi secret values, GitHub App credentials, user tokens, or Firestore binding records. Infrastructure jobs require particular review because they can alter public endpoints, identity permissions, or persistent data.

## Labels

Every label-capable GCP resource managed for this product uses `product=github_oauth_worker`, `service=github-oauth-worker`, and its `environment` label. This supports cost and inventory filtering across the shared GCP project.
