# CMS OAuth Worker Contract

## Runtime Modes

`bootstrap` exposes health and setup guidance only. `ready` requires the GitHub App OAuth credentials, state-signing secret, public HTTPS worker origin, and environment policy. The runtime does not connect to Firestore.

## Decap Login

A comic's CMS configuration sets `backend_auth_endpoint = "auth/<repository_id>"`, where the ID is GitHub's immutable numeric repository ID. Decap opens `GET /auth/<repository_id>` and refreshes at `POST /auth/<repository_id>/refresh?provider=github`. This selector is not proof of authorization.

The login popup captures the opener's actual `MessageEvent.origin`. `POST /auth/<repository_id>/handshake` signs that origin and repository ID into short-lived OAuth state, protected by an HttpOnly correlation cookie, before redirecting to GitHub. The callback checks the signed state, exchanges the code for a user token, evaluates the GitHub-login policy, and verifies the user's App installation includes the selected repository.

Before returning a token, the worker reads `.comic_git/cms_access.toml` through GitHub's Contents API on the repository's **current default branch**. It validates the file's schema, repository ID, worker origin, and approved browser origins. A missing, invalid, mismatched, or unreadable policy denies the token. The callback posts token data only to the signed exact origin. The worker never uses Decap's advisory `site_id` or a branch supplied by the website as authority.

Refresh repeats the user policy, repository access, and live default-branch policy checks before releasing a new token. CORS preflight permits a syntactically valid HTTPS Origin so the browser can make the POST; preflight is not authorization. A denied POST returns no token and includes only a sanitized error and narrow CORS headers for that request origin. A removed policy or revoked GitHub access therefore blocks the next refresh, though previously issued GitHub tokens remain valid until GitHub expires or revokes them.

Two repositories can approve the same origin and remain separate by repository ID. This does not isolate JavaScript or browser storage between sites sharing one origin; users wanting that isolation need separate origins, such as custom domains.

## Guided Setup

`GET /setup` starts GitHub App discovery and repository/branch selection. The worker verifies the user has administrator authority before preparing changes. Setup always presents the current default-branch origins and proposed approval for explicit review. It signs the selected origins, default-branch revision, and generated plan digest; confirmation rechecks GitHub authorization and the reviewed revisions.

The engine runner produces a versioned plan from the selected repository branch. A legacy INI site receives TOML files. An editable TOML site receives only needed repairs to worker-owned `[cms]` fields, preserving comments and unrelated settings. A site failing the engine's CMS editing checks is not automatically repaired. The worker creates or updates a CMS migration PR if files need to change, and a default-branch access-policy PR if the policy needs to change. When the target is the default branch, both changes can share one PR. The policy PR must merge before CMS login can succeed.

The setup session is short-lived, encrypted, bound to the selected installation and repository, and used only to reduce repeated GitHub account prompts. It stores no refresh token. The worker relies on GitHub branch/PR state rather than a second database for migration history.

## Operator Policy

`ACCESS_POLICY=public` is for the comic_git-operated shared service. Independently hosted workers must use `github_login_whitelist`. Both modes still require GitHub repository access and a matching default-branch policy before releasing tokens.
