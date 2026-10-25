# Architecture

github_oauth_worker is a self-hostable GitHub App OAuth bridge for comic_git Decap CMS sites. It returns a short-lived GitHub user token only when the user can access the selected repository and that repository's default-branch policy authorizes the requesting website origin.

| Component | Responsibility |
| --- | --- |
| FastAPI | Guided CMS setup, Decap OAuth callback and refresh, health endpoint. |
| GitHub client | OAuth exchange, App installation and repository membership, current policy read, branch and PR writes during setup. |
| Access policy | Exact HTTPS origin, repository ID, and worker-origin approval from `.comic_git/cms_access.toml` on the current default branch. |
| Engine migration runner | Versioned TOML migration and narrow CMS settings repair in an isolated workspace. |
| Pulumi | Separate test and production Cloud Run, GitHub App credentials, identities, and deployment resources. |

## Token Boundary

The published comic sets `backend_auth_endpoint` to `auth/<numeric repository ID>`. The ID routes the request but grants nothing by itself. The popup records the opener's actual origin and signs it into browser-correlated OAuth state. After GitHub authorization, the worker checks the optional login whitelist, the user's App access to that repository, and the live default-branch access policy. Only then does it post credentials to that exact origin. Refresh repeats these checks. Details are in [the worker contract](features/cms-oauth/worker-contract.md).

A default-branch policy read is a security boundary: the site cannot select an old branch with stale approval. If a default branch changes, login fails until the policy exists on the new default branch. Two repositories may share a GitHub Pages origin but keep separate policies by ID; sharing an origin still shares browser storage.

## Setup Boundary

Setup verifies GitHub administrator access, obtains explicit origin approval, and uses the selected engine's migration runner to prepare changes. It proposes a policy PR against the default branch and a CMS migration PR against the selected branch when each is needed. Both changes can share one PR when the branches are the same. GitHub branches and PRs are the source of truth for review state. The short-lived setup session is only for avoiding unnecessary OAuth prompts.

## Deployment Boundary

Test and production have separate Cloud Run services, GitHub Apps, credentials, and runtime identities in one GCP project. The worker stores no repository bindings, user tokens, or migration state in a database. See [infrastructure](infrastructure.md).
