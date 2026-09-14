<!-- ai-agent-toolkit:managed version="1.0.0" -->

# CMS OAuth

This feature lets a comic_git site use Decap CMS with GitHub through a dedicated OAuth worker. It deliberately separates authentication from comic content validation: comic_git_engine validates supported CMS TOML before it generates `admin/`, while this worker authenticates the editor who is using that CMS.

## Goals

- Give Decap editors a GitHub App user token with only the App's installed repositories and granted permissions.
- Make shared hosting and independent self-hosting practical without source changes.
- Default to a GitHub-login whitelist for self-hosted workers, with a policy seam that can later support paid access.
- Keep the public service stateless, inexpensive, and safe to operate at low traffic.

## Non-Goals

- Manage comic content, parse TOML, or reproduce the engine's CMS compatibility validation.
- Store editor sessions or GitHub tokens after a request completes.
- Provide a general GitHub proxy or repository API.
- Automate GitHub App ownership consent or installation.

## Documents

- [Worker contract](worker-contract.md) - Decap endpoints, security rules, access policy, and deployment/bootstrap boundary.
- [GitHub App registration](github-app-registration.md) - Versioned App settings, permissions, setup/update verification, and policy boundaries.
