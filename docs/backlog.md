# Worker Backlog And Release Checks

This is the single index for unfinished worker work. Detailed contracts and test steps remain in the linked docs. Check off or remove items as they are completed; do not treat a passing CI run as a substitute for the manual release checks.

## Before The First Public CMS Release

- [ ] Complete CI rollout: the test service-scoped deployment grant is applied, but CI needs the bootstrap stack's `run.operations.get` custom role to poll Cloud Run updates. Review and owner-apply bootstrap to add that role and remove the ineffective project-wide grants, then verify approval-gated test apply. Owner-apply the normal production stack to establish its service-scoped grant before verifying production apply. Preview identities and all four GitHub Environments are configured; both apply Environments require reviewers. Preview identities may write only Pulumi lock objects, not stack state or cloud resources. See [CI/CD](cicd.md) and [infrastructure setup](../infra/README.md#github-actions-configuration).
- [ ] Deploy and validate the merged worker against the test GitHub App and real `comic_git_dev` branches. Run the numbered [setup checklist](features/cms-oauth/setup-manual-verification.md), including **B** (`/setup` before App installation), login and token refresh, policy-only setup, and two-PR setup for a non-default CMS branch. Record the tested worker and engine revisions.
- [ ] Test the [shared-origin access rules](features/cms-oauth/README.md#ga-readiness) with two repositories and users under one GitHub Pages owner. Confirm that login and refresh verify the selected repository's App-installation membership and access policy, not merely public repository metadata.
- [ ] Set a dedicated migration commit author name and email. Preserve the initiating creator in audit logs and the PR description; commit metadata must not be presented as proof of authorization.
- [ ] Add an accessible, named progress view for repository reads, engine resolution, migration planning, CMS settings, commits, and PR creation. Polish setup/review/result/error pages as one workflow.
- [ ] Replace the generic expired-review JSON response with a clear page and a same-repository, same-branch retry that performs fresh authorization and review. Invalid or tampered state must not preselect a target.
- [ ] Complete production GitHub App, worker URL, IAM, secrets, and deploy validation; confirm the published CMS can log in, refresh, edit, and publish. Follow [GitHub App registration](features/cms-oauth/github-app-registration.md) and [infrastructure](infrastructure.md).
- [ ] Document the recommended `/setup`-first creator flow and App-install-first alternative in the public comic_git guidance. The worker's current contract is in [CMS enablement](features/cms-oauth/cms-enablement.md).

## Deferred Infrastructure Work

- [ ] Simplify first-time infrastructure setup with a small owner-run helper that guides state-bucket setup, bootstrap stack initialization, KMS secrets-provider migration, and normal-stack initialization. Keep the bootstrap and deployment stacks separate so CI cannot redefine its own security boundaries; see [infrastructure setup](../infra/README.md#bootstrap).

## Deferred Product Work

- [ ] Evaluate a production custom domain and external Application Load Balancer, including DNS, TLS, routing, and the worker's public URL. Do this before publishing a permanent custom worker URL; see the [CMS OAuth feature backlog](features/cms-oauth/README.md#deferred-work).
- [ ] Explore setup-driven maintenance and repair for already-enabled sites, including optional legacy-file cleanup and a dashboard that remains reachable when Decap is broken. Refine the provisional [dashboard exploration](features/cms-oauth/management-dashboard.md) before implementation.
- [ ] Consider a direct-commit CMS enablement mode only as an explicit product choice after the PR flow is proven; see [CMS enablement](features/cms-oauth/cms-enablement.md).
- [ ] Update starter-page guidance so newly migrated sites do not direct creators to edit superseded INI, post text, or transcript files. Coordinate the user-facing change with `comic_git` and `comic_git_docs`.
