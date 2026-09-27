<!-- ai-agent-toolkit:managed version="1.0.0" -->

# CMS OAuth

This feature lets a comic_git site use Decap CMS with GitHub through a dedicated OAuth worker. It deliberately separates authentication from comic content validation: comic_git_engine validates supported CMS TOML before it generates `admin/`, while this worker authenticates the editor who is using that CMS.

## Goals

- Give Decap editors a GitHub App user token with only the App's installed repositories and granted permissions.
- Turn a verified GitHub App installation into a reviewable, engine-owned CMS migration pull request so creators can enable CMS without manually converting legacy content.
- Make shared hosting and independent self-hosting practical without source changes.
- Default to a GitHub-login whitelist for self-hosted workers, with a policy seam that can later support paid access.
- Keep the public service stateless, inexpensive, and safe to operate at low traffic.

## Non-Goals

- Reimplement comic content migration, parse TOML independently, or reproduce the engine's CMS compatibility validation.
- Store editor sessions or GitHub tokens after a request completes.
- Provide a general GitHub proxy or repository API.
- Bypass GitHub App owner consent, repository selection, or an explicit creator confirmation before writing a migration pull request.

## GA Readiness

- Support multiple repositories for one GitHub owner without making each editor repeat setup. Define the browser trust boundary first: GitHub Pages project sites under one owner share an origin, and neither a URL path nor a client-supplied repository selector isolates their JavaScript or Decap's origin-wide auth storage. Decide whether same-origin repositories are one trusted group or require distinct origins for independent security. Then make login, refresh, binding lookup or its replacement, and migration behavior consistent with that decision; test distinct repositories and users sharing an owner.
- Verify on every authorization and refresh that GitHub lists the bound repository within the selected App installation for that user token. Fetching public repository metadata by ID alone does not prove installation membership. Keep the separate question of which repository permission level qualifies an editor explicit.
- Set migration commit author name and email to a dedicated worker identity without implying that metadata changes the authenticated GitHub actor. Keep the creator as the initiating actor in worker audit logs and reference that person in the PR description.
- Present a named, accessible installation-progress view while the worker performs repository reads, engine resolution, migration planning, CMS configuration, commit creation, and pull-request creation. It must clearly distinguish a completed step, the active step, and a recoverable failure without exposing repository content or credentials.
- Replace the generic JSON error for an expired CMS migration review with a clear expiry page and a retry action that resumes with the same repository and target branch. Retry must obtain fresh authorization, recheck access and branch availability, and rebuild the review from the current target state; an invalid or tampered state must not provide a preselected retry.
- Polish the installation, review, result, and error pages as a coherent workflow, including spacing, action labels, and accessible feedback.

## Deferred Work

- Provision a global external Application Load Balancer for the production worker before assigning its custom domain.
- Map the production worker to a stable custom domain such as `oauth.comic-git.com`. Manage the domain, TLS, DNS records, load-balancer routing, and worker public URL through the production infrastructure configuration before publishing that URL to users.
- On App installation or update, validate and reconcile worker-owned CMS configuration so reinstalling the App repairs incorrect repository, branch, or worker endpoint settings. Evaluate other narrowly scoped, safe automatic repairs separately.
- Update the default starter-page guidance after CMS migration so it does not direct CMS users to edit legacy `info.ini`, `post.txt`, or transcript files that TOML now supersedes.

## Documents

- [Worker contract](worker-contract.md) - Decap endpoints, security rules, access policy, and deployment/bootstrap boundary.
- [GitHub App registration](github-app-registration.md) - Versioned App settings, permissions, setup/update verification, and policy boundaries.
- [CMS enablement](cms-enablement.md) - Creator-facing installation and migration behavior.
