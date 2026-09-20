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

## Deferred Work

- Provision a global external Application Load Balancer for the production worker before assigning its custom domain.
- Map the production worker to a stable custom domain such as `oauth.comic-git.com`. Manage the domain, TLS, DNS records, load-balancer routing, and worker public URL through the production infrastructure configuration before publishing that URL to users.
- Use the GitHub App installation token, rather than the creator's OAuth token, for migration branches, commits, and pull requests after the creator's authorization has been verified. This should attribute repository changes to the App while preserving the creator as the initiating actor in worker audit logs.
- On App installation or update, validate and reconcile worker-owned CMS configuration so reinstalling the App repairs incorrect repository, branch, or worker endpoint settings. Evaluate other narrowly scoped, safe automatic repairs separately.
- Update the default starter-page guidance after CMS migration so it does not direct CMS users to edit legacy `info.ini`, `post.txt`, or transcript files that TOML now supersedes.

## Documents

- [Worker contract](worker-contract.md) - Decap endpoints, security rules, access policy, and deployment/bootstrap boundary.
- [GitHub App registration](github-app-registration.md) - Versioned App settings, permissions, setup/update verification, and policy boundaries.
- [CMS enablement](cms-enablement.md) - Creator-facing installation and migration behavior.
