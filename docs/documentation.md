<!-- ai-agent-toolkit:managed version="1.2.0" -->
<!-- Audience: Developers and AI agents adding or modifying documentation.
     Purpose: Explain the docs structure, the philosophy behind it, and how to decide
     where new content belongs. Read this before creating a new doc file. -->

# Documentation Guide

## Philosophy

Docs that drift from reality are worse than no docs. The goal is accuracy over completeness.

- **Don't document what the code already makes obvious.** Document *why*, not *what*.
- **Don't duplicate.** If a doc should reference another, link to it.
- **Don't add docs for unchanged behavior.** This creates drift and noise.
- **Do document sharp edges, non-obvious decisions, and things that silently break.**

### When is something worth documenting at all?

Before deciding *where* something belongs, decide *whether* it belongs anywhere persistent — a
comment, a docstring, a help-string, or any doc below. Only write it down when skipping it would do
one of two things:

- **Invite a mistake.** A future editor could plausibly "simplify" or revert a deliberate, non-obvious
  choice back to the seemingly-more-correct version, reintroducing a bug.
- **Invite real confusion.** Even if nobody ever touches it, a reader would plausibly stop and burn
  genuine time wondering "why is this like this?" before concluding it's deliberate.

This is Chesterton's Fence in both directions: someone tears the fence down without knowing why it
was built (mistake), or someone doesn't touch it but wastes real time staring at it, wondering why
it's there (confusion). Either failure mode alone justifies documenting something; if neither applies,
don't.

If the reasoning is just a preference, a decision made in the moment, or something freely reversible
by whoever's reading it (e.g. a CLI flag's default value, when the flag itself is freely overridable),
it belongs in the commit message, MR description, or Jira ticket — not in a comment or a doc. It
explains *this change, right now*, not something a future reader needs in order to not re-break or
misjudge the code. A good tell: if the justification only makes sense with "the conversation/PR that
produced it" as context, it belongs in that context, not in a persistent doc or comment.

Once something clears this bar, find where it belongs: a comment right at the relevant code if the
*why* is tied to one specific spot, or one of the docs below (see the Quick Decision Guide) if it's
bigger than that — spans several files, is about how the system as a whole is shaped, or belongs to
a specific feature. Don't default to `docs/decisions/` just because something clears the bar; use it
only when nothing else below already topically owns the content.

## Structure

```
docs/
├── *.md                 — Evergreen, repo-wide reference docs (setup, architecture, testing, etc.)
├── decisions/           — One doc per significant decision, kept even after it's superseded
└── features/            — One subfolder per feature or product area, retired with the feature
```

### Root `docs/` — Evergreen repo-wide docs

For documentation that applies to the whole repo indefinitely and doesn't have a natural expiry:
dev setup, architecture, testing conventions, coding principles, debugging. These are
maintained continuously and should always reflect current reality.

**Add a new root-level doc when** a topic is repo-wide and doesn't fit any existing file.
Prefer adding a section to an existing doc over creating a new one.

### `docs/decisions/` — Architectural Decision Records

Decision docs are the [Philosophy](#philosophy) bar applied to one specific choice, rather than the
system's overall shape.

Distinct from `docs/architecture.md`: a decision doc is a dated record of one specific choice (often
a rejected alternative), while `architecture.md` is the living, continuously-updated explanation of
why the system as a whole is shaped the way it is. General "why is it built like this?" confusion
about the system's structure belongs in `architecture.md`; a specific choice with real reversal risk
gets its own decision doc.

**Create a decision doc when** a single choice clears the bar above — most often because you're
accepting a known limitation or tradeoff that a future editor could plausibly "fix" without knowing why.

Use `_template.md`. Name files `YYYY-MM-DD-short-title.md`. Add to the index in `README.md`.

### `docs/features/` — Feature-scoped docs

For documentation tied to a specific feature or product area that has a natural lifecycle — it may
become stale, be superseded, or be retired when the feature is.

Each feature gets its own subfolder with a `README.md` index. Use the `_template/` directory to
bootstrap a new feature folder.

**What belongs here:** Content that captures *intent and requirements* — what a feature is supposed
to do, why it behaves a certain way, business rules and edge cases that aren't obvious from the
code. This is information an agent or developer can't reliably derive by reading the implementation.

**What does NOT belong here:** Implementation details — component structure, data flow, which hooks
call which functions. The code is the source of truth for those. Documenting them creates high-drift
content that will eventually mislead more than it helps.

Ask before adding a feature doc: *"Would reading the code answer this question?"* If yes, skip the
doc. If the code can't express the intent, requirements, or business reasoning — document it.

**Don't use `docs/features/` for** architectural principles, setup steps, or anything that
applies across the whole codebase.

## After Completing Work

When a feature or fix is done, the right question is not "should I preserve the plan?" — it's "what did this work produce that the code can't express on its own?"

- **Run `/update-docs`** to update existing docs that reflect changed behavior, interfaces, or conventions.
- **Write a `docs/decisions/` entry** if the choice clears the bar in [Philosophy](#philosophy). The reasoning in the plan is ephemeral; a decision doc is permanent.
- **Write a `docs/features/` entry** if there is intent, requirements, or business rules behind the feature that can't be derived from reading the implementation — applying the intent-over-implementation rule.
- **Don't preserve `specs/` files.** They are scaffolding. Once the work is merged, delete them.

## Quick Decision Guide

| Content type                                                               | Where it goes               |
|----------------------------------------------------------------------------|-----------------------------|
| How to set up the dev environment                                          | `docs/dev_setup.md`         |
| Why we made a non-obvious architectural choice                             | `docs/decisions/`           |
| Why a feature behaves a certain way (intent, requirements, business rules) | `docs/features/<feature>/`  |
| A new failure mode or diagnostic command                                   | `docs/debugging.md`         |
| A coding convention that applies repo-wide                                 | `docs/coding-principles.md` |
| System components, data flow, and why the system is structured this way    | `docs/architecture.md`      |

## Format Conventions

All managed docs start with the `ai-agent-toolkit:managed` comment block:

```html
<!-- ai-agent-toolkit:managed version="1.0.0" -->
<!-- Audience: who reads this doc
     Purpose: what this doc is for and what it covers -->
```

The audience and purpose comment is important — it tells agents (and humans) whether a doc is
relevant to their current task without them having to read the whole thing.
