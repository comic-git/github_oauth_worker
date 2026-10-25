# CMS Setup Manual Verification

Use this checklist against a deployed test worker and test GitHub App. **B is the recommended first-time creator path:** start at `/setup`, then follow the worker's link to install the App. D verifies the supported alternate path that starts on GitHub. The IDs and step numbers are references for reporting a failure (for example, "B, step 5"). A-E cover the ordinary setup paths; F-N need specific accounts, repository states, or browser changes and can be run separately. The expected worker page headings are literal; GitHub controls its own page wording and may omit an account chooser or return immediately when the browser is already authorized. **Continue with GitHub** always redirects through GitHub authorization, even if the App is already installed for the repository. An unexpected extra GitHub authorization *after repository selection* is a failure only in a case that explicitly expects direct branch selection.

## Preparation

- Deploy the worker version under test and use its `/setup` URL. Confirm its `/health` endpoint reports `ready` and its `githubAppSlug` points to the matching test App.
- Use test repositories and branches only. For successful migration cases, the selected branch should contain supported legacy comic_git content and an engine selector accepted by the test worker. The GitHub user must have administrator access to the repository.
- Keep the App's **Redirect on update** setting enabled. When installing the App for a test, select only the intended repository rather than granting access to all repositories.
- Tests that create or update a pull request change the test repository. Steps marked **optional write** may be stopped at the review page.
- Start each case in a fresh `/setup` visit unless the case says otherwise. If switching GitHub identities in one browser becomes confusing, use a separate browser profile. A setup form and its encrypted session normally expire after 10 minutes.

## Core Flows

### A. Already-connected repository without a migration PR

| Step | Action | Expected result |
|------|--------|-----------------|
| 1 | Open `/setup`. | Worker: **Enable comic_git CMS**, with **Continue with GitHub** and no **Use another GitHub account** button. |
| 2 | Click **Continue with GitHub**. | GitHub: **Authorize comic-git-oauth-worker-test** for the test App, with an account choice if required, or an immediate callback if GitHub already has authorization. The App being installed does not skip this worker redirect. |
| 3 | Continue as the intended GitHub user. | Worker: **Choose comic repository**, listing repositories visible through that user's App installations. |
| 4 | Select an already-connected repository and click **Continue with selected repository**. | Worker: **Select comic branch** directly. No second GitHub authorization while the discovery session and access checks succeed. |
| 5 | Select a branch and click **Review CMS migration**. | Worker: **Review CMS migration**, showing the selected repository, target branch, engine selector, resolved engine commit, and proposed files. |
| 6 (optional write) | Click **Create migration pull request**. | Worker: **CMS migration is ready**, with a link to the new GitHub pull request. The PR targets the selected branch. |

### B. First App installation from setup

| Step | Action | Expected result |
|------|--------|-----------------|
| 1 | With no App installations visible to this user, open `/setup`. | Worker: **Enable comic_git CMS** with **Continue with GitHub**. |
| 2 | Click **Continue with GitHub**. | GitHub: App authorization, with an account choice if required. |
| 3 | Continue as the intended GitHub user. | Worker: **Choose comic repository**, with **No repositories are connected to this GitHub App for your account yet.** and **Add repository access on GitHub**. |
| 4 | Click **Add repository access on GitHub**. | GitHub: account/organization selection as needed, followed by the App installation screen. The generic link does not promise to skip GitHub's account chooser. |
| 5 | Install the App for one test repository. | GitHub returns to the worker at `/setup?installation_id=...`; worker: **Enable comic_git CMS** with **Continue**. |
| 6 | Click **Continue**. | Worker: **Select comic repository** for that verified installation. If the earlier discovery token cannot see the new installation, GitHub authorization appears first. |
| 7 | Select the newly connected repository and click **Review CMS migration**. | Worker: **Select comic branch** directly when the installation authorization can verify administrator access; otherwise one repository-restricted GitHub authorization may occur first. |
| 8 | Select a branch and continue. | Worker: **Review CMS migration** with the correct repository, branch, and file list. |

### C. Add a repository to an existing installation

| Step | Action | Expected result |
|------|--------|-----------------|
| 1 | Open `/setup`. | Worker: **Enable comic_git CMS** with **Continue with GitHub**. |
| 2 | Click **Continue with GitHub**. | GitHub: App authorization, with an account choice if required. |
| 3 | Continue as the intended GitHub user. | Worker: **Choose comic repository**; existing App-connected repositories are listed, but the new repository is absent. |
| 4 | Click **Add repository access on GitHub** and choose the account or organization where the App is installed. | GitHub opens that installation's configuration rather than creating an unrelated installation. |
| 5 | Add one test repository to the App and save the change. | GitHub redirects to `/setup?installation_id=...` when **Redirect on update** is enabled. |
| 6 | Click **Continue**. | Worker: **Select comic repository**, including the newly added repository. A GitHub authorization detour is allowed if the old discovery token cannot see the updated installation. |
| 7 | Select the new repository. | Worker: **Select comic branch** after administrator access is verified, or a repository-restricted authorization followed by that page. |

### D. Direct GitHub installation without visiting setup first

| Step | Action | Expected result |
|------|--------|-----------------|
| 1 | In a fresh browser profile, follow the test App's GitHub installation URL and install it for one test repository. | GitHub redirects to `/setup?installation_id=...`; worker: **Enable comic_git CMS**. |
| 2 | Click **Continue**. | GitHub asks the user to authorize the App, because this browser has no discovery session. |
| 3 | Finish authorization. | Worker: **Select comic repository**, listing repositories in that installation. |
| 4 | Select the test repository. | Worker: **Select comic branch** directly, without another GitHub authorization when the new token verifies access. |

### E. Select a non-default branch

| Step | Action | Expected result |
|------|--------|-----------------|
| 1 | Follow A through **Select comic branch**. | The repository's default branch is preselected; other accessible branches are offered. |
| 2 | Choose a different eligible branch and continue. | **Review CMS migration** names that branch as **Target branch** and compares files against its current revision. |
| 3 (optional write) | Create the pull request. | The GitHub PR base is the chosen branch, not the repository's default branch. |

## Recovery And Access

### F. Switch GitHub user

| Step | Action | Expected result |
|------|--------|-----------------|
| 1 | Follow A through **Choose comic repository**. | Worker shows **Use another GitHub account**. |
| 2 | Click that button. | GitHub is asked to show its account picker; the prior setup session is cleared. |
| 3 | Choose another permitted user and authorize if prompted. | Worker returns to **Choose comic repository** with only repositories visible to the newly chosen user's App access. Previous selections do not carry over. |

### G. Missing discovery session at selection

| Step | Action | Expected result |
|------|--------|-----------------|
| 1 | Follow A through **Choose comic repository**. | The repo list is visible. |
| 2 | In browser developer tools, delete only the `cms_setup_session` cookie, then promptly select a repository. Do not wait for the signed form state to expire. | GitHub authorization is requested instead of using the missing session. |
| 3 | Authorize the same user. | Worker: **Select comic branch** after repository access is verified. |

### H. User denied by worker policy

| Step | Action | Expected result |
|------|--------|-----------------|
| 1 | On a whitelist-mode test worker, open `/setup` and authorize a GitHub user absent from its whitelist. | Worker: **comic_git CMS setup could not continue**, with **This GitHub account is not permitted to use this service.** No repository list or migration action is shown. |

### I. GitHub user lacks repository administrator access

| Step | Action | Expected result |
|------|--------|-----------------|
| 1 | Use an App-connected test repository where the authorized user can see the repo but is not an administrator. Follow A through repo selection. | The worker does not show a migration review or create a PR. It may request a fresh repository authorization before showing an error. |
| 2 | Complete that authorization if asked. | Worker: **comic_git CMS setup could not continue** with **GitHub could not verify access to the configured repository.** |

### J. Unsupported engine or already-enabled branch

| Step | Action | Expected result |
|------|--------|-----------------|
| 1 | Select a branch whose engine version is older than the worker's CMS minimum. | After branch selection, the worker reports that the engine version is too old, with troubleshooting code `engine_version_too_old`; no PR is created. |
| 2 | Separately, select a branch already migrated to TOML and enabled for this repository, branch, and worker. It may retain legacy INI files. | Worker: **comic_git CMS is already enabled**, with a message that no migration pull request is needed. The setup flow still offers an access-policy pull request if the current default branch does not authorize this website. |
| 3 | Select a TOML-backed branch whose CMS is disabled, or whose enabled CMS settings point to another repository, branch, or worker. | Worker reviews a narrow repair to worker-owned `[cms]` settings, preserving other TOML content and comments. Confirming the review creates or refreshes the migration pull request. |
| 4 | Select a TOML-backed branch with matching CMS settings but a page that fails the engine's CMS editing checks. | Worker shows `cms_not_ready` and does not create a PR. The engine build provides the file-level readiness details. |

## Pull Request Lifecycle

Use a dedicated test repository for these cases. GitHub branch and PR state, not Firestore, determines the review outcome.

### K. Existing migration PR is up to date

| Step | Action | Expected result |
|------|--------|-----------------|
| 1 | Leave an open migration PR for an unchanged target branch, then repeat A through branch selection. | Worker: **Review CMS migration** with **Existing pull request: Review on GitHub**. |
| 2 | Inspect the review page. | **The migration pull request is up to date.** No **Update migration pull request** button is shown. |

### L. Existing migration PR needs a refresh

| Step | Action | Expected result |
|------|--------|-----------------|
| 1 | Use a test PR whose migration branch differs from the current engine-produced result while the target branch has not advanced. Repeat A through branch selection. | Worker: **Review CMS migration**, a link to the existing PR, and a **Changes to the pull request** table comparing against that PR's current branch. |
| 2 (optional write) | Click **Update migration pull request**. | Worker: **CMS migration is ready**, linking the same PR; GitHub shows a new refresh commit on its branch. |
| 3 | Repeat setup without changing either branch. | The review page reports the PR is up to date, as in K. |

### M. Target branch advances after a migration PR exists

| Step | Action | Expected result |
|------|--------|-----------------|
| 1 | Keep an open migration PR, then add a normal commit to its target branch. Repeat A through branch selection. | Worker: **Review CMS migration** for a new PR and a notice linking the older PR that will be closed. |
| 2 (optional write) | Click **Create migration pull request**. | A replacement PR is created for the new target revision; the older migration PR is closed. Its branch is deleted unless another open PR still uses it. |

### N. Target changes after review but before confirmation

| Step | Action | Expected result |
|------|--------|-----------------|
| 1 | Reach **Review CMS migration** on a test branch, then commit to the target branch in another session before clicking Create or Update. | The old review remains visible, but it is no longer valid. |
| 2 | Click the review page's Create or Update button. | The worker refuses the stale plan with troubleshooting code `cms_migration_review_changed`; it does not silently write the old migration result. Restart setup to review the new state. |
