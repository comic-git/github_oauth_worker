<!-- ai-agent-toolkit:managed version="1.0.0" -->
<!-- Audience: comic_git creators and developers.
     Purpose: Define the user-facing CMS enablement outcome after GitHub App installation. -->

# Planned CMS Enablement

The intended CMS enablement behavior makes installing the comic_git GitHub App the first step in converting a legacy comic_git repository. The App installation asks the creator to select only the repository they want to manage. GitHub then redirects to the worker, where the creator authorizes their GitHub account, verifies the selected repository, chooses the branch to prepare, reviews the conversion summary, and explicitly creates a migration pull request. The repository's default branch is preselected, but the creator can choose another branch that GitHub exposes to their authorized App token.

The pull request makes the selected branch CMS-ready by retaining its legacy files, adding engine-generated TOML replacements, and configuring the CMS to use the worker. The worker resolves the repository's accepted engine-version selector once to an official engine commit and records that SHA in the pull request for auditability; it does not change the repository's selected engine version. The creator reviews and merges the pull request using normal GitHub controls. After the site deploys, the creator opens the CMS and completes the existing origin-bound login flow.

The worker rejects repositories that cannot be migrated safely, including unsupported repository layouts, missing or incompatible engine selectors, partial migrations, unreadable source content, or a user without repository-administrator authority. Errors tell the creator what needs attention without exposing private repository content or credentials.

The default delivery is a pull request because CMS enablement can affect every page metadata file. A future direct-to-default-branch mode may reduce friction for trusted use cases, but it must remain an explicit product choice rather than an implicit side effect of installation.
