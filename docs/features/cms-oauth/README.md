<!-- ai-agent-toolkit:managed version="1.0.0" -->

# CMS OAuth

This feature lets a comic_git site use Decap CMS with GitHub through a dedicated OAuth worker. It deliberately separates authentication from comic content validation: comic_git_engine validates supported CMS TOML before it generates `admin/`, while this worker authenticates the editor who is using that CMS.

## Goals

- Give Decap editors a GitHub App user token with only the App's installed repositories and granted permissions.
- Let creators start at the worker's `/setup` URL, connect a repository to the GitHub App when needed, and review an engine-owned CMS migration pull request without manually converting legacy content.
- Make shared hosting and independent self-hosting practical without source changes.
- Default to a GitHub-login whitelist for self-hosted workers, with a policy seam that can later support paid access.
- Keep the public service stateless, inexpensive, and safe to operate at low traffic.

## Non-Goals

- Reimplement comic content migration, parse TOML independently, or reproduce the engine's CMS compatibility validation.
- Store editor sessions or GitHub tokens server-side. CMS enablement may use only a short-lived encrypted, HttpOnly browser cookie for its interactive setup steps; normal Decap login does not create a worker session.
- Provide a general GitHub proxy or repository API.
- Bypass GitHub App owner consent, repository selection, or an explicit creator confirmation before writing a migration pull request.

## GA Readiness

- Validate the shared-origin security contract with browser tests. GitHub Pages project sites under one owner share an origin, so sites at that origin must be treated as one trusted browser group even though each repository has its own access policy. Test distinct repositories and users sharing an owner.
- Verify on every authorization and refresh that GitHub lists the selected repository within an App installation accessible to the user token. Fetching public repository metadata by ID alone does not prove installation membership. Keep the separate question of which repository permission level qualifies an editor explicit.
- Set migration commit author name and email to a dedicated worker identity without implying that metadata changes the authenticated GitHub actor. Keep the creator as the initiating actor in worker audit logs and reference that person in the PR description.
- Present a named, accessible installation-progress view while the worker performs repository reads, engine resolution, migration planning, CMS configuration, commit creation, and pull-request creation. It must clearly distinguish a completed step, the active step, and a recoverable failure without exposing repository content or credentials.
- Replace the generic JSON error for an expired CMS migration review with a clear expiry page and a retry action that resumes with the same repository and target branch. Retry must obtain fresh authorization, recheck access and branch availability, and rebuild the review from the current target state; an invalid or tampered state must not provide a preselected retry.
- Polish the installation, review, result, and error pages as a coherent workflow, including spacing, action labels, and accessible feedback.

## Deferred Work

- Provision a global external Application Load Balancer for the production worker before assigning its custom domain.
- Map the production worker to a stable custom domain such as `oauth.comic-git.com`. Manage the domain, TLS, DNS records, load-balancer routing, and worker public URL through the production infrastructure configuration before publishing that URL to users.
- Evaluate additional narrowly scoped maintenance and repair tasks beyond the worker-owned CMS settings handled during setup.
- Update the default starter-page guidance after CMS migration so it does not direct CMS users to edit legacy `info.ini`, `post.txt`, or transcript files that TOML now supersedes.
- Explore a management dashboard for already-enabled sites, maintenance tasks, and recovery without requiring a working Decap editor. The design is provisional; see [CMS management dashboard exploration](management-dashboard.md).

## Documents

- [Worker contract](worker-contract.md) - Decap endpoints, security rules, access policy, and deployment/bootstrap boundary.
- [GitHub App registration](github-app-registration.md) - Versioned App settings, permissions, setup/update verification, and policy boundaries.
- [CMS enablement](cms-enablement.md) - Creator-facing installation and migration behavior.
- [CMS setup manual verification](setup-manual-verification.md) - Numbered page-flow checks for installed, new, recovery, and PR lifecycle cases.
- [CMS management dashboard exploration](management-dashboard.md) - Provisional maintenance use cases, possible UI locations, and authorization questions.
- [CMS binding authority exploration](binding-store-exploration.md) - Historical reasoning and prototype, not operational guidance.
- [Repository CMS access policy](access-policy-setup.md) - Policy format, creator confirmation, and same-branch or two-branch setup behavior.
