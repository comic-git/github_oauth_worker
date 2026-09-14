<!-- ai-agent-toolkit:managed version="1.1.0" -->

# CMS OAuth Worker Contract

## Service Modes

In `bootstrap` mode, the worker exposes health and setup guidance only. In `ready` mode, it requires valid App credentials and Firestore access before authorizing or enrolling users.

## Decap Authorization

`GET /auth`, `GET /callback`, and `POST /auth/refresh` implement Decap's GitHub authentication contract. The initial popup handshake captures the actual `MessageEvent.origin` of the CMS opener. The worker uses that origin to look up an active binding; it never treats Decap's `site_id` query parameter as proof of origin ownership.

A successful callback sends `authorization:github:success:<json>` from the worker origin to the exact active CMS origin captured in signed state. Error callbacks use the same exact target. The worker must never use `postMessage(..., '*')` for a token-bearing result.

The worker exchanges a GitHub authorization code with the binding's repository ID, then verifies with GitHub's user installation/repository APIs that the returned token actually includes the repository. This verification is required because a requested repository ID may be ignored when it is unavailable. Refresh repeats that verification.

Refresh resolves its binding from the browser request's exact `Origin` header, not Decap's advisory `site_id`. The worker returns CORS headers only for that active bound origin, then rechecks policy and repository access before returning refreshed credentials.

## Self-Service Enrollment

An unknown CMS origin may begin enrollment. The worker captures the actual origin, authenticates the prospective owner, evaluates policy, shows the canonical origin and selected repository for confirmation, and creates a binding only after verifying that the user's App installation includes that repository.

The initial enrollment token is used only to list verified installation/repository choices and is discarded before confirmation. Selecting a repository starts a fresh GitHub authorization; the worker verifies that second token against the selected installation and repository immediately before creating the binding and returning Decap credentials.

The public GitHub App has a setup URL and redirects to it after installation changes. Setup `installation_id` values are untrusted input. The worker must require fresh user authorization and verify the user can access both the installation and selected repository before creating or updating a binding.

## Binding Lifecycle

A binding is keyed by repository ID and contains active origin records, installation metadata, mutable owner/repository display metadata, status, and non-secret audit timestamps. One repository may have multiple active origins.

Normal domain migration starts from an existing active origin, creates a short-lived pending origin, and activates it only after a popup from that exact new origin completes the handshake. The old origin remains active for the configured grace period. Lost-origin recovery, repository transfers, and changed App repository selection require fresh explicit setup/enrollment verification.

## Access Policy

`ACCESS_POLICY=github_login_whitelist` is the default. `GITHUB_LOGIN_WHITELIST` is a deployer-controlled case-insensitive list; an empty list denies all users. For third-party deployments, this is the only supported policy mode. Do not set `ACCESS_POLICY=public` on a third-party worker.

`ACCESS_POLICY=public` exists only for the comic_git-operated shared service, where self-service enrollment is part of the product. It still requires a verified repository/origin binding and does not let arbitrary browser origins receive tokens.

Policy is evaluated during enrollment, normal authorization, and refresh. Removing a login prevents new enrollment and subsequent token refresh, but cannot instantly revoke a previously issued GitHub token; expiring user tokens keep that remaining exposure bounded.

## GitHub App and Data Handling

The App requests repository contents read/write and pull-request read/write permissions. Owners select repositories at installation. The worker holds only the App client ID/client secret needed for the user authorization flow; it does not hold an App private key or create installation tokens in the MVP.

The worker does not persist access tokens, refresh tokens, authorization codes, cookies, state values, raw headers, or comic content. Logs contain safe request metadata and failure categories only.
