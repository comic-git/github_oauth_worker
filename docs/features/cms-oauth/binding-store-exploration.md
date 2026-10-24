# Can CMS Login Work Without Firestore?

The proposed direction is to keep CMS access approval in the repository, on its default branch. This is a design agreement, not a change to the running worker. The prototype does not change login or remove Firestore.

## Terms Used Here

- **Token:** A temporary credential that lets Decap CMS use GitHub as the signed-in editor. The worker must be careful which website receives it.
- **Origin:** The protocol, domain, and optional port of a website. For example, `https://comic-git.github.io` is an origin. The path after the domain is *not* part of it, so `https://comic-git.github.io/comic-a/` and `https://comic-git.github.io/comic-b/` have the same origin.
- **Binding:** A trusted statement that a particular website may receive a token for a particular GitHub repository.
- **Repository ID:** GitHub's number for a repository. Unlike its owner and name, this number stays the same when the repository is renamed or transferred.
- **Branch:** A named line of work in a GitHub repository, such as `master` or `cms`. Different branches can contain different versions of the same settings file.
- **Policy:** The proposed settings in a repository that say which website origins may use its CMS.

## Why Any Binding Is Needed

Imagine Alice can edit Comic B's repository, but she visits Comic A's website. Comic A should not be able to ask the worker for Alice's Comic B token. GitHub can confirm that Alice has access to Comic B; it cannot tell whether Comic A's website is an appropriate place to send that token. The worker must check both facts before returning it.

The website can tell the worker which repository it wants, but that request is not proof. The worker also needs a trusted source saying that this website is allowed to use that repository.

## What Firestore Does Today

Firestore stores the worker's trusted website-to-repository bindings. It also stores the GitHub App installation number associated with each repository, information about website address changes, and a lookup that lets login find a repository from the website's origin. The worker checks this information during login and token refresh. Firestore does **not** track the state of CMS migration pull requests; GitHub itself does that.

The current lookup assumes that one origin belongs to one repository. That is a problem for two comics published at `https://owner.github.io/comic-a/` and `https://owner.github.io/comic-b/`: they have different repositories but the same origin. Fixing this is necessary whether we keep Firestore or remove it.

## Three Possible Approaches

| Approach | How the worker knows which site belongs to a repository | Main tradeoff |
| --- | --- | --- |
| Keep a smaller Firestore record | Store the approved origin and repository ID. Ask GitHub for current App installation information instead of trusting a saved installation number. | The existing setup flow mostly stays the same, but we still run a database and must fix the shared-origin lookup. |
| Put the approval in the repository | Read a settings file from the repository's current default branch that lists the origins allowed to use it. | No binding database, but login needs another GitHub read and setup may need a second pull request. |
| Put a signed approval on the website | The worker signs an approval when the site is set up, and checks that signature at login. | Fast to check, but an old approval can remain valid after the owner removes a domain unless we add expiry or another lookup. |

The repository-settings approach is the agreed direction for removing Firestore. It makes the repository owner responsible for the approval, with GitHub providing the file to the worker. The remaining questions below still need answers before it can replace the running system.

## What the Prototype Shows

The [prototype](../../../prototypes/repository_config_binding.py) tests a small part of the repository-settings approach. The [tests](../../../tests/prototypes/test_repository_config_binding.py) use example `comic_info.toml` files with two proposed `[cms]` settings: `repository_id` and `authorized_origins`. That was a convenient test input, **not** the agreed file layout. The intended approval is a separate file, provisionally `.comic_git/cms_access.toml`, committed to the repository's default branch. It would hold the repository ID, approved origins, worker address, and selected CMS branch; its exact format is not finalized.

The prototype compares those settings with a supplied repository ID, website origin, and worker address. In a complete worker, GitHub would have to verify the repository ID, and the login popup would have to provide the website's actual origin. The tests reject a website that copied another site's settings, a request for a different repository, and settings intended for a different worker. They also show that two comics can name the same GitHub Pages origin while selecting different repositories by ID.

That last result solves **routing**, not browser isolation. Two sites under one origin can access the same browser storage. A malicious page in one comic could still interact with the other comic's browser data. This is a property of sharing an origin, not something this prototype fixes.

The prototype reads only example data. It does not fetch files from GitHub, return a token, change comic_git's settings format, or affect the deployed worker. Passing these tests does not mean the complete login flow is secure.

## How a Complete Version Might Work

1. comic_git puts the repository ID into Decap's login address, for example `auth/123456`. That tells the worker which repository to investigate; it does not authorize anything by itself. Decap would use the same address for token refresh. This path also avoids relying on Decap's special handling of `site_id` on localhost.
2. When the user starts login, the worker records the actual origin of the website that opened the login popup. It asks GitHub to sign in the user, then checks that both the user and the GitHub App can access the requested repository.
3. The worker asks GitHub which branch is currently the repository's default, then reads the access file **only from that branch**. It checks the repository ID, worker address, and website's actual origin. It also checks that CMS is enabled and ready on the selected CMS branch before returning a token. Token refresh repeats these checks. The website cannot choose an older branch's copy of the access file.
4. Setup compares the intended access file with the current one. If it is missing **or needs an update**, setup creates an access-policy pull request targeting the default branch. It also creates the CMS migration pull request targeting the selected CMS branch when migration is needed. If both changes target the default branch, they belong in one pull request. If the current access file is already correct, setup creates no access-policy pull request.
5. When there are two pull requests, the worker presents them as one setup task with separate links and clear status. The suggested merge order is access policy first, migration second, so login is ready when the migrated site goes live. The worker still refuses login while the selected CMS branch is not ready. If GitHub is unavailable or either required file is missing or invalid, login fails without sending a token.

## How This Handles Old Branches

Suppose Comic B's owner removes `https://old-domain.example` from today's settings, but an older branch still lists it. If a website can ask the worker to read that old branch, the old domain can continue receiving Comic B tokens. The prototype includes a test showing this failure: changing one copy of the settings does not change an older copy.

The agreed answer to "Which copy of the settings controls login?" is the copy on GitHub's **current default branch**. The website does not get to choose a different branch for this check. The comic itself may still run CMS from another branch, such as `cms`; the worker checks that branch's CMS readiness separately. An old branch containing an old approval does not count. If the default branch changes and the new one lacks the access file, login fails until setup repairs it.

## Other Questions Before Making the Change

- How does setup find the right initial website address for GitHub Pages, a custom domain, or another host? How can the creator correct it before merging the access-policy pull request?
- How do existing CMS sites gain the new repository ID and access file without a confusing manual migration?
- How should setup report partial progress if one of two pull requests is merged, closed, or changed? It must not imply that a missing policy or an unready CMS branch is already usable.
- Are comics sharing one GitHub Pages origin intentionally treated as one trusted browser group? The repository ID can distinguish their login requests, but cannot give them separate browser storage.
- Will reading GitHub on every login and refresh be fast and reliable enough? We should not keep an old approval in a cache long enough to undermine removal of a domain.
- What should setup do when a repository changes owner or a domain changes hands? Removing access to the old domain must be dependable.

## Current Recommendation

Keep Firestore in the deployed worker until the default-branch access-file flow is implemented and tested. The access policy and CMS migration should be reviewable as one setup process, using one pull request when they target the same branch and two only when they target different branches and both need changes. Once the worker can safely read the current access file, verify GitHub permissions, and handle partial setup, the Firestore binding can be removed. Until then, the existing worker remains the fallback rather than treating the prototype as production-ready.
