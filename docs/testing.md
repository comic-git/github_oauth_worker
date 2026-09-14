<!-- ai-agent-toolkit:managed version="1.0.0" -->
<!-- Audience: AI agents writing or running tests, and developers. -->

# Testing

## Current State

No test suite exists until the application scaffold is added. The first implementation must add executable commands here rather than leaving them as placeholders.

## Test Structure

The planned layout is:

```text
tests/unit/          Pure configuration, state, policy, and response-format tests.
tests/http/          FastAPI route tests with the GitHub HTTP boundary simulated.
tests/integration/   Explicitly marked tests for a manually provisioned GitHub App and Cloud Run sandbox.
```

## Testing Philosophy

Every behavioral or security change requires automated coverage. Tests must exercise real application validation, state signing, policy evaluation, and Decap callback HTML; they may mock only GitHub's HTTP API and GCP runtime services at the network boundary.

The normal suite must not contact GitHub, Cloud Run, Secret Manager, or a real repository. Live GitHub App tests are opt-in and use a dedicated sandbox account, App, and repository.

## Required Initial Coverage

- Reject unsupported providers, invalid `site_id` values, missing state, expired state, and state/cookie mismatches.
- Verify authorization-code and refresh exchanges use bounded HTTP requests and that sensitive values never appear in errors or logs.
- Verify whitelist matching is case-insensitive, empty whitelists deny access, and policy runs at enrollment, authorization, and token refresh.
- Verify Firestore binding lookup, setup installation-ID verification, repository-access verification, origin migration, and lost-origin recovery with simulated boundaries.
- Verify success and failure callback pages emit Decap-compatible `postMessage` payloads to an exact bound origin, never a wildcard target.
- Verify the health endpoint exposes no secret configuration.

## Failing Tests

A failing test is evidence of a regression until the implementation and intended contract have both been reviewed. Do not change a test merely to accommodate behavior that was not deliberately approved.
