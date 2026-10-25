<!-- ai-agent-toolkit:managed version="1.0.0" -->
<!-- Audience: Maintainers exploring post-setup repository management.
     Purpose: Preserve provisional goals and open design questions without committing to an implementation. -->

# CMS Management Dashboard (Exploration)

This is an early design note, not an implementation plan or an approved product contract. Expand and refine it before choosing a UI location, an API, or an authorization model.

## Why Explore It

The setup flow currently treats an already-enabled, CMS-ready branch as a successful no-op. That same entry point could eventually offer maintenance actions rather than stopping there. Candidate actions include deleting legacy files after TOML migration, enabling CMS on an existing TOML site, disabling CMS output, changing CMS settings, and repairing settings that prevent CMS login. Each action needs its own scope and safety rules; recognizing an already-enabled site does not itself authorize a change.

The management entry point must remain usable when Decap or the generated `/admin/` page is broken or disabled. A comic_git site could link to it from near its CMS link, possibly supplying a repository ID to preselect the site. That ID would be an untrusted navigation hint, never proof of repository ownership or permission. The user and the selected repository and branch would still need GitHub authorization and fresh access checks before data is shown or changed.

## Possible UI Locations

| Option | Potential benefit | Question or cost |
|--------|-------------------|------------------|
| Worker-hosted management pages, extending `/setup` | Reuses the existing guided authorization and confirmation flow; remains accessible if the site CMS fails. | How much repository-management responsibility should the OAuth worker take on? |
| Static `/dashboard` in the deployed comic_git site, calling a worker API | Keeps the management entry point alongside the creator's site and could preload site identity. | Requires a browser-facing API, explicit CORS and token handling, and a separate way in when the site itself cannot be reached. The static site cannot supply trusted authorization on its own. |
| A Decap CMS extension or widget | Could keep routine controls beside content editing. | Cannot be the only management path if CMS login or configuration is broken or CMS output is disabled. |

These are alternatives to investigate, not a commitment to one combined design. A small worker-hosted maintenance flow may be a useful first experiment; a broader dashboard or a separate management service should be considered if the worker's responsibilities grow.

## Questions Before Design

- Which maintenance actions are worth shipping, and which are better left as documented local commands? Does disabling CMS only stop admin output while TOML remains authoritative?
- How should the dashboard and Decap share or reuse GitHub authorization so creators are not asked to sign in twice, without weakening either flow's repository access checks?
- How should a management link behave for sites with multiple comics, branches, repositories, or GitHub Pages sites sharing one origin?
- Which actions should produce a reviewable pull request, which can be applied directly, and what file preview and explicit confirmation must precede legacy-file deletion or configuration changes?
- How can an owner recover when CMS settings are wrong or the deployed site is unavailable, without relying on the broken CMS to reach its repair controls?
- If browser JavaScript calls a worker API, what exact origin, repository, user, and operation checks are required for each request? How are credentials held and refreshed?

Do not treat the current `/setup` success result, an embedded repository ID, or access to a static dashboard page as sufficient authorization for maintenance operations.
