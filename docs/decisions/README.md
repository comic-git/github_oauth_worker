<!-- ai-agent-toolkit:managed version="1.1.0" -->
<!-- Audience: AI agents and developers.
     Purpose: Explain what this folder is and how to use it.
     This file is read by the /implement and /review agents before they start work. -->

# Decisions

This folder documents significant decisions made in this codebase — architectural choices, library
selections, design tradeoffs, and anything else where understanding *why* matters as much as *what*.

The name references Chesterton's Fence: don't remove a fence before understanding why it was built.

## For AI Agents

**Before modifying any file**, search this folder for decisions that list that file in their
"Files affected" field. If a relevant decision exists, read it before proceeding. Making a change
that contradicts an active decision requires explicit human confirmation.

To search: look for the file path or directory name in `*.md` files in this folder.

## For Developers

See `docs/documentation.md`'s "When is something worth documenting at all?" for the general test
(prevents a mistake, or prevents real confusion) and where things go if this isn't the right folder.
This folder is only one of several destinations that test can point to — reserve it for a rejected
alternative or a choice with real reversal risk. General "why is the system built this way" confusion
belongs in `docs/architecture.md` instead; immediate "why we did it this way" context with no ongoing
power to prevent a future mistake belongs in the Jira ticket, not here.

Create a decision doc when:
- A plausible alternative approach exists, was deliberately rejected, and someone could reasonably
  reach for that alternative again without knowing why it doesn't work
- Code exists that *looks* removable, redundant, or wrong without context (a Chesterton's Fence) —
  and removing or "fixing" it would reintroduce a real problem
- You're accepting a known tradeoff or limitation that a future dev needs to know about before
  changing related code
- A choice is surprising enough that future devs would otherwise waste real time confirming it's
  intentional, even if they'd never actually change it

Do NOT create a decision doc for:
- Historical trivia about a choice that nothing depends on anymore (e.g. framework/library
  selection where the codebase itself makes the current state obvious)
- Implementation detail that's already clear from reading the code — document *why*, not *what*
- One-time operational notes (a migration's pre-deploy checklist, a specific backfill's scope-check
  query) that apply once and then are done — those belong in the ticket, or in the code/script
  itself if they're needed at runtime
- Decisions that can be tracked in a small comment at an obvious anchor point in the code

Use `_template.md` to create a new doc. Name files `YYYY-MM-DD-short-title.md`.

## Index

<!-- List decision docs here as they are created, newest first.
     Format: - [Short title](filename.md) — one-line summary, status -->
