<!-- ai-agent-toolkit:managed version="1.0.0" -->
<!-- Audience: AI agents working on CI/CD pipelines, and developers. -->

# CI/CD

## Pipeline

Automated jobs never apply infrastructure:

1. `test` creates a venv, installs locked development dependencies, and runs formatting, static checks, and the offline test suite on commits and pull requests.
2. `pulumi-preview-test` depends on `test` and automatically previews the test stack for trusted repository branches and pull requests.
3. `pulumi-preview-production` depends on `test` and automatically previews the production stack on `master`. The production WIF provider currently trusts only `master`; fork pull requests must not receive deployment credentials.

Separate manually started jobs apply the test or production stack. The operator reviews the automatic preview before deliberately dispatching an apply for a named environment and ref. The apply job uses that environment's identity and runs a fresh preview for the selected ref before `pulumi up`; the initial automatic preview is not a promise that cloud state stayed unchanged. A push, pull request, successful preview, or PR merge must never start an apply by itself. Production applies are limited to `master`; test applies may use a selected trusted branch. GitHub Environment approval may add another gate but is not a substitute for manual job activation. This repository currently has one developer, so the workflow does not require copying a preview identifier into the apply job.

## Credentials and Boundaries

Test and production use separate GitHub Apps, Cloud Run services, runtime service accounts, and Secret Manager secrets. CI test credentials may access only the test environment. Production credentials are available only to the manually dispatched production workflow.

CI must never print Pulumi secret values, GitHub App credentials, or user tokens. Infrastructure jobs require particular review because they can alter public endpoints, identity permissions, or persistent data.

## Labels

Every label-capable GCP resource managed for this product uses `product=github_oauth_worker`, `service=github-oauth-worker`, and its `environment` label. This supports cost and inventory filtering across the shared GCP project.
