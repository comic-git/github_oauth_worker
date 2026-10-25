<!-- ai-agent-toolkit:managed version="1.0.0" -->
<!-- Audience: AI agents working on CI/CD pipelines, and developers. -->

# CI/CD

## Pipeline

The workflow starts automatically; infrastructure changes wait for a reviewer:

1. `test` creates a venv, installs locked development dependencies, and runs formatting, static checks, and the offline test suite on commits and pull requests.
2. `pulumi-preview-test` depends on `test` and automatically previews the test stack for trusted repository branches and pull requests.
3. `pulumi-preview-production` depends on `test` and automatically previews the production stack on `master`. The production WIF provider currently trusts only `master`; fork pull requests must not receive deployment credentials.

After a successful preview on an eligible branch, the matching test or production apply job enters a GitHub Environment approval gate. It must not call `pulumi up` until an authorized reviewer approves that job. Production applies are limited to `master`; test applies may follow trusted branches. The apply job rechecks the selected ref and current stack before updating because an earlier preview does not freeze cloud state. Rejecting a pending approval leaves the deployment unapplied. As a single-developer repository, do not enable GitHub's **Prevent self-review** setting unless another reviewer is available.

Preview jobs must not reference the protected apply environments: GitHub would pause those jobs too. CI implementation must provide separate, unprotected preview configuration and read-only cloud identity; the current deployment identities must not be exposed to automatic pull-request code. Fork pull requests may run offline tests but must not receive cloud credentials. Keep pending approvals from old revisions from deploying stale code, without interrupting an apply already in progress.

## Credentials and Boundaries

Test and production use separate GitHub Apps, Cloud Run services, runtime service accounts, and Secret Manager secrets. CI test credentials may access only the test environment. Production apply credentials are available only after approval of a job on `master`; automatic previews use narrower identities.

CI must never print Pulumi secret values, GitHub App credentials, or user tokens. Infrastructure jobs require particular review because they can alter public endpoints, identity permissions, or persistent data.

## Labels

Every label-capable GCP resource managed for this product uses `product=github_oauth_worker`, `service=github-oauth-worker`, and its `environment` label. This supports cost and inventory filtering across the shared GCP project.
