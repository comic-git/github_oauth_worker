<!-- ai-agent-toolkit:managed version="1.2.0" -->
<!-- Audience: AI agents writing code, and developers doing code review. -->

# Coding Principles

## Python Service

- Target the latest stable Python version supported by Cloud Run, CI, and required dependencies. At MVP implementation time, that version is Python 3.14.
- Use Pydantic v2 and `pydantic-settings` at untrusted runtime boundaries: environment configuration, HTTP input, GitHub API responses, Firestore records, and Decap protocol payloads. Configure models to reject unexpected fields where forward compatibility is not explicitly required.
- Keep HTTP route handlers thin. Put validation, OAuth state, policy, binding, and GitHub communication in separately testable modules.
- Use ordinary typed data structures for already-validated internal values; do not use repeated Pydantic conversion as a substitute for clear domain logic or unit tests.
- Treat all browser input and GitHub responses as untrusted until validated. Semantic checks such as exact-origin matching and repository-binding verification remain explicit code, not schema-only validation.
- Use a small exception hierarchy that maps expected user-facing failures to generic safe responses. Unexpected failures must not expose configuration or token data.
- Keep network calls behind a single client boundary with explicit connect and total timeouts.

## Names and Domain Terms

- Use `github_login`, `user_access_token`, `refresh_token`, `authorization_code`, `site_id`, `binding`, and `access_policy` consistently. Do not shorten tokens to ambiguous names such as `auth` or `credential`.
- Use `github_login_whitelist` and `GITHUB_LOGIN_WHITELIST` for the default restricted policy. `public` is reserved for the comic_git-operated shared service and must be described as unsafe for ordinary third-party deployments.
- Use `*_seconds` for duration values and `is_*`, `has_*`, or `can_*` for booleans.
- Prefer nouns for data objects and verb phrases for operations, such as `exchange_authorization_code` and `is_login_whitelisted`.

## Comments and Documentation

Apply [`docs/documentation.md`](documentation.md)'s Philosophy section to comments and docstrings. Write them when omitting the explanation would invite a mistake or real confusion; keep them focused on the present constraint, contract, or rationale.

Public modules, classes, functions, and methods should have concise docstrings when their purpose, parameters, return values, exceptions, side effects, or security contract are not obvious from their signature and local context. Avoid narrative comments that merely restate code or preserve irrelevant editing history.

## Security and Observability

- Never include secrets or credentials in source, committed configuration, exceptions, logs, metrics labels, or test snapshots.
- Log safe classifications and request metadata, not raw request bodies or headers.
- Test security-relevant validation directly; tests complement Pydantic validation and never replace it.

## Infrastructure

- Keep Pulumi resources and application code separate. Infrastructure configuration must name the target stack, environment, project, and Firestore database explicitly.
- Use Pulumi secret values and GCP Secret Manager for sensitive values. Do not use a checked-in default for any credential.
- Apply `product=github_oauth_worker`, `service=github-oauth-worker`, and `environment` labels to every label-capable managed resource.
- Prefer the smallest GCP privilege set that can run the worker and read its own secrets and database.
