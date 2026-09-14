<!-- ai-agent-toolkit:managed version="1.0.0" -->

# CMS OAuth Worker Contract

## Decap Endpoints

| Endpoint             | Behavior                                                                                                                                                                  |
|----------------------|---------------------------------------------------------------------------------------------------------------------------------------------------------------------------|
| `GET /healthz`       | Returns a non-secret service health response.                                                                                                                             |
| `GET /auth`          | Accepts only `provider=github`, validates `site_id`, creates short-lived state, and redirects the popup to GitHub.                                                        |
| `GET /callback`      | Validates callback state, exchanges the authorization code, evaluates the access policy, and returns HTML that posts Decap's success or error result to the popup opener. |
| `POST /auth/refresh` | Accepts the GitHub App refresh token from Decap, refreshes it, repeats the access-policy check, and returns the token response JSON.                                      |

The successful callback must send Decap's `authorization:github:success:<json>` message from the configured worker origin to the exact configured CMS site origin selected during `/auth`. Decap does not provide the worker with the opener's origin, so the worker derives the target from its trusted CMS-origin configuration, not from the query string or a wildcard target.

## GitHub App

The App requests repository contents read/write and pull-request read/write permissions. The App's installation selects repositories, and the editor's own permissions further restrict its user token. No webhook or App private key is needed for this initial user-authorization worker; client ID and client secret are sufficient.

GitHub App user access tokens should expire and use refresh tokens. The worker sends the user token to Decap because Decap's GitHub backend needs it, but the worker must neither persist nor log either token.

## Access Policy

`ACCESS_POLICY=github_login_allowlist` is the default. `ALLOWED_GITHUB_LOGINS` is a comma-separated, case-insensitive deployer-controlled list; an empty list denies all users. The policy applies after the initial code exchange and after every refresh. `ACCESS_POLICY=public` is an explicit opt-in for self-hosted operators who do not want a service-level restriction.

`ALLOWED_CMS_ORIGINS` is a required comma-separated list of exact CMS origins. The worker accepts a `site_id` only when it maps to one configured origin, stores that origin in signed callback state, and uses it as the callback page's `postMessage` target. This prevents an unrelated page from collecting a returned token through a popup it opened. The CMS-origin allowlist is distinct from the editor-login access policy.

The Decap `/auth` request includes only `site_id`, not the configured GitHub repository. The worker cannot safely enforce a repository allowlist at that endpoint. Repository authorization instead comes from the GitHub App installation and GitHub's user permission checks.

## GitHub App Registration

Pulumi manages GCP resources, not GitHub App registration. GitHub requires an App owner to consent to requested permissions and generate credentials that must be handled as secrets. The repository will provide a versioned registration checklist and settings template as the authoritative configuration reference.

For a self-hosted worker, the operator first provisions the Cloud Run service and receives its public callback URL, creates an App in GitHub's settings using the versioned checklist, stores the generated client ID and client secret in the configured secret store, then updates the service configuration and installs the App on the intended repository. A shared hosted worker follows the same process once under the operator's account; site owners install that App in their own repositories.

## Security Requirements

- Accept only GitHub as an identity provider and reject malformed inputs before redirecting.
- Accept `site_id` only when it maps to a configured CMS origin and never use a wildcard callback `postMessage` target.
- Use short-lived signed state and a Secure, HttpOnly, SameSite=Lax correlation cookie; clear the cookie after callback processing.
- Set bounded outbound GitHub HTTP timeouts and return generic browser-safe failures.
- Log request metadata and safe failure classifications only. Never log authorization codes, state values, cookies, client secrets, access tokens, or refresh tokens.
- Use Secret Manager bindings to give only the Cloud Run service account access to runtime secrets.
