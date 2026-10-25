# Infrastructure

The service uses two Cloud Run deployments in one GCP project: test and production. They have separate GitHub Apps, OAuth secrets, runtime service accounts, and Artifact Registry repositories. Each service scales to zero and uses GitHub as the authority for repository access and default-branch CMS policy. No database is required by the runtime.

Pulumi Python lives under `infra/`. The owner-run bootstrap stack creates stable identity, KMS, registry, secrets, and WIF boundaries. The normal `test` and `production` stacks build a Docker image and deploy its digest to Cloud Run. The state bucket is created manually because it is needed before Pulumi can manage anything else. See [the setup guide](../infra/README.md) for exact commands and variables.

The first deployment of each normal stack is owner-run. It creates the Cloud Run service and grants only that environment's CI identity service-level Cloud Run Admin access. Later GitHub Actions deployments use that grant; the bootstrap stack does not grant project-wide Cloud Run administration.

## Database Boundary

The worker and bootstrap program manage no Firestore databases or database-scoped IAM grants. CMS website access is approved by `.comic_git/cms_access.toml` on each repository's current default branch. The shared GCP project's Firestore API stays enabled because other services may rely on it.

Deploy a compatible engine migration contract before the policy-backed worker. Run setup to review the CMS configuration and default-branch access-policy pull requests, merge the required pull requests, then rebuild the site with `auth/<repository_id>` and verify Decap login and refresh. Existing sites using the old endpoint will not work with the policy-backed worker until updated.
