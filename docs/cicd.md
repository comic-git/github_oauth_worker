<!-- ai-agent-toolkit:managed version="1.0.0" -->
<!-- Audience: AI agents working on CI/CD pipelines, and developers. -->

# CI/CD

## Pipeline

The workflow starts automatically. Infrastructure previews run without approval; apply jobs wait for a reviewer. Configure the owner-run bootstrap stack and all four GitHub Environments as described in [infrastructure setup](../infra/README.md#github-actions-configuration). Previews will fail until their identities and environment variables are available.

1. `test` creates a venv, installs locked development dependencies, and runs formatting, static checks, and the offline test suite on commits and pull requests.
2. `pulumi-preview-test` depends on `test` and automatically previews the test stack for trusted repository branches and pull requests.
3. `pulumi-preview-production` depends on `test` and automatically previews the production stack on `master`. The production WIF provider currently trusts only `master`; fork pull requests must not receive deployment credentials.

After a successful preview on an eligible branch, the matching test or production apply job enters a GitHub Environment approval gate. It must not call `pulumi up` until an authorized reviewer approves that job. Production applies are limited to `master`; test applies may follow trusted branches. The apply job rechecks the selected ref and current stack before updating because an earlier preview does not freeze cloud state. Rejecting a pending approval leaves the deployment unapplied. As a single-developer repository, do not enable GitHub's **Prevent self-review** setting unless another reviewer is available.

Preview jobs use unprotected `test-preview` and `production-preview` Environments; apply jobs use protected `test` and `production`. GitHub would pause previews if they referenced a protected Environment. The bootstrap stack gives preview identities cloud-resource read access, KMS decrypt access for Pulumi secrets, and GCS write access only to Pulumi lock objects. This is not a fully read-only identity: a Pulumi DIY backend needs a lock even for preview. Never expose deployment identities to automatic pull-request code. Fork pull requests run offline tests only. Apply jobs recheck the branch SHA after approval, and shared per-stack concurrency avoids overlapping Pulumi operations without canceling an apply already in progress.

## Credentials and Boundaries

Test and production use separate GitHub Apps, Cloud Run services, runtime service accounts, and Secret Manager secrets. CI test credentials may access only the test environment. Production apply credentials are available only after approval of a job on `master`; automatic previews use separate identities. Preview jobs can decrypt stack configuration and therefore run only for trusted same-repository code, never fork PR code.

CI must never print Pulumi secret values, GitHub App credentials, or user tokens. Infrastructure jobs require particular review because they can alter public endpoints, identity permissions, or persistent data.

## Labels

Every label-capable GCP resource managed for this product uses `product=github_oauth_worker`, `service=github-oauth-worker`, and its `environment` label. This supports cost and inventory filtering across the shared GCP project.
