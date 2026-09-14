<!-- ai-agent-toolkit:managed version="1.2.0" -->
<!-- Audience: AI agents writing code, and developers doing code review. -->

# Coding Principles

## Python Service

- Target Python 3.12 and use complete type annotations for application boundaries, configuration, and GitHub response models.
- Keep HTTP route handlers thin. Put validation, OAuth state, access policy, and GitHub communication in separately testable modules.
- Use explicit configuration models with validation at startup. Never read environment variables throughout business logic.
- Treat all browser input and all GitHub responses as untrusted until validated.
- Use a small exception hierarchy that maps expected user-facing failures to generic safe responses. Unexpected failures must not expose configuration or token data.
- Keep network calls behind a single client boundary with explicit connect and total timeouts.

## Names and Domain Terms

- Use `github_login`, `user_access_token`, `refresh_token`, `authorization_code`, `site_id`, and `access_policy` consistently. Do not shorten tokens to ambiguous names such as `auth` or `credential`.
- Use `*_seconds` for duration values and `is_*`, `has_*`, or `can_*` for booleans.
- Prefer nouns for data objects and verb phrases for operations, such as `exchange_authorization_code` and `is_login_allowed`.

## Security and Observability

- Never include secrets or credentials in source, committed configuration, exceptions, logs, metrics labels, or test snapshots.
- Add comments only for non-obvious security or protocol constraints, especially Decap popup compatibility and callback-state handling.
- Log safe classifications and request metadata, not raw request bodies or headers.

## Infrastructure

- Keep Pulumi resources and application code separate. Infrastructure configuration must name the target stack and project explicitly.
- Use Pulumi secret values and GCP Secret Manager for sensitive values. Do not use a checked-in default for any credential.
- Prefer the smallest GCP privilege set that can run the worker and read its own secrets.
