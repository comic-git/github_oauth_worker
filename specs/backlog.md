# Worker Backlog And Release Checks

This is the single index for unfinished worker work. Detailed contracts and test steps remain in the linked docs. Check off or remove items as they are completed; do not treat a passing CI run as a substitute for the manual release checks.

## Before The First Public CMS Release

- [ ] Implement CI in a separate PR after the CMS access-policy PR merges: run Ruff and the offline tests on PRs, automatically preview test changes on trusted branches/PRs and production changes on `master`, and require manual workflow activation for either environment's apply. The intended pipeline and credential boundaries are in [CI/CD](../docs/cicd.md).
- [ ] Deploy and validate the merged worker against the test GitHub App and real `comic_git_dev` branches. Run the numbered [setup checklist](../docs/features/cms-oauth/setup-manual-verification.md), including **B** (`/setup` before App installation), login and token refresh, policy-only setup, and two-PR setup for a non-default CMS branch. Record the tested worker and engine revisions.
- [ ] Test the [shared-origin access rules](../docs/features/cms-oauth/README.md#ga-readiness) with two repositories and users under one GitHub Pages owner. Confirm that login and refresh verify the selected repository's App-installation membership and access policy, not merely public repository metadata.
- [ ] Set a dedicated migration commit author name and email. Preserve the initiating creator in audit logs and the PR description; commit metadata must not be presented as proof of authorization.
- [ ] Add an accessible, named progress view for repository reads, engine resolution, migration planning, CMS settings, commits, and PR creation. Polish setup/review/result/error pages as one workflow.
- [ ] Replace the generic expired-review JSON response with a clear page and a same-repository, same-branch retry that performs fresh authorization and review. Invalid or tampered state must not preselect a target.
- [ ] Complete production GitHub App, worker URL, IAM, secrets, and deploy validation; confirm the published CMS can log in, refresh, edit, and publish. Follow [GitHub App registration](../docs/features/cms-oauth/github-app-registration.md) and [infrastructure](../docs/infrastructure.md).
- [ ] Document the recommended `/setup`-first creator flow and App-install-first alternative in the public comic_git guidance. The worker's current contract is in [CMS enablement](../docs/features/cms-oauth/cms-enablement.md).

## Deferred Product Work

- [ ] Evaluate a production custom domain and external Application Load Balancer, including DNS, TLS, routing, and the worker's public URL. Do this before publishing a permanent custom worker URL; see the [CMS OAuth feature backlog](../docs/features/cms-oauth/README.md#deferred-work).
- [ ] Explore setup-driven maintenance and repair for already-enabled sites, including optional legacy-file cleanup and a dashboard that remains reachable when Decap is broken. Refine the provisional [dashboard exploration](../docs/features/cms-oauth/management-dashboard.md) before implementation.
- [ ] Consider a direct-commit CMS enablement mode only as an explicit product choice after the PR flow is proven; see [CMS enablement](../docs/features/cms-oauth/cms-enablement.md).
- [ ] Update starter-page guidance so newly migrated sites do not direct creators to edit superseded INI, post text, or transcript files. Coordinate the user-facing change with `comic_git` and `comic_git_docs`.
