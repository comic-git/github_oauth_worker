<!-- ai-agent-toolkit:managed version="1.4.0" -->
<!-- Audience: Developers working in this repo, human and AI. -->

# Contributing

## Branching

Create a focused branch from the current default branch for implementation, infrastructure, or documentation changes that are more than a trivial correction. Name it for the outcome, such as `github-app-worker` or `harden-callback-state`. A branch tracks and pushes to a same-named branch on its intended remote; its base and push destination are separate decisions.

## Commits

Use concise, imperative commit subjects that describe one coherent change. Do not mix application behavior, unrelated cleanup, or generated artifacts into the same commit. Never commit `.env` files, Pulumi secret configuration, credentials, tokens, or manual test output containing sensitive values.

## Review and Merge Requirements

Before requesting review, run the relevant offline tests and static checks, update durable documentation for changed user-facing behavior or security constraints, and read the entire diff. OAuth, access-policy, GitHub App permission, Pulumi, and secret-handling changes require explicit human review. Do not push, merge, alter CI/CD, or deploy without human confirmation.

## Working With AI

For a defined feature, use the sequence plan, implement, test, documentation update, then review, with human review between stages. Planning files are local scaffolding and are ignored by Git; preserve the durable feature, architecture, setup, and decision documentation that remains useful after implementation.

Agents must read `AGENTS.md`, relevant documentation, and applicable decision records before editing. The repository has no installed specialist agent that matches Pulumi Python; use the normal engineering workflow rather than a Terraform-specific agent for Pulumi changes.

## Developer Responsibility

The reviewer is accountable for every changed line, including AI-authored code. In particular, verify that tests still describe intended behavior, browser responses do not leak secrets, GitHub permissions are minimal, and infrastructure changes target the correct project and stack.
