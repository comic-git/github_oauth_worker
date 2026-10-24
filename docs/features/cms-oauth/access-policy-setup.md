# Repository CMS Access Policy

This is the setup contract for moving CMS website approval from Firestore into the comic repository. Policy creation is opt-in through `CMS_ACCESS_POLICY_SETUP_ENABLED=true` while the new flow is tested. The default setup path and live CMS login still use Firestore; this flag does not switch token authorization to the repository policy.

## Policy File

The owner reviews `.comic_git/cms_access.toml` as an ordinary repository change. A proposed version 1 file looks like this:

```toml
schema_version = 1
repository_id = 123456789
cms_branch = "cms"
worker_origin = "https://oauth.example.com"
authorized_origins = ["https://creator.github.io", "https://comic.example.com"]
```

`repository_id` is GitHub's numeric repository ID, which remains stable across renames. `cms_branch` is the creator-selected branch whose CMS readiness the worker will check. `worker_origin` identifies the worker this policy authorizes. `authorized_origins` lists exact HTTPS browser origins allowed to receive that repository's CMS token. A URL path is not an origin: two GitHub Pages project sites under `https://creator.github.io` share that origin and browser storage.

At login and token refresh, the worker must read this file from GitHub's **current default branch**. Neither the website nor the selected CMS branch may choose which copy supplies approval. The worker must compare the file's repository ID with GitHub's verified ID, its worker origin with its own configured origin, and the browser's actual origin with the approved list before returning a token. A missing, invalid, or inaccessible file denies access. The reader and validator currently implement only this read boundary; they are not connected to token delivery.

## Creator Confirmation

After the creator chooses a repository and CMS branch, setup should show the current default-branch approval, if valid, alongside the proposed origins. For a new file, setup may suggest the site's HTTPS origin using the comic domain, `CNAME`, or GitHub Pages address, but it must not quietly approve that guess. The creator confirms the complete origin list and can add or remove addresses. An existing valid list should be prefilled so a repeated setup does not accidentally revoke an address. Show the worker address and selected CMS branch in the same review.

The proposal planner canonicalizes origins, rejects HTTP, paths, empty lists, and duplicates, and writes deterministic TOML using `tomli-w`. Formatting or list order alone does not cause an update. A malformed existing policy or one naming a different repository blocks automatic replacement; the creator must inspect it. A valid policy for another worker may be replaced only through explicit administrator review. Setup must re-read the default branch and rebuild the proposal immediately before writing so a changed branch or policy cannot silently invalidate the review.

## Pull Requests

| Selected CMS branch | Policy differs from default branch? | Setup result |
| --- | --- | --- |
| Default branch | Yes | Include the policy file in the CMS migration PR, or create a policy-only PR if CMS is already ready. |
| Another branch | Yes | Create a policy PR targeting the default branch and, if needed, a migration PR targeting the selected CMS branch. Present both as one setup task. |
| Either | No | Do not create or refresh a policy PR. Create a migration PR only if CMS still needs changes. |

The policy PR must be merged before a newly migrated site can log in. Merging the CMS PR or deploying the site cannot substitute for default-branch approval. If the repository's default branch changes, setup must review the policy on the new default branch. An old branch containing a former approval must never restore access.

The opt-in setup flow presents an explicit origin form, signs the approved list and reviewed policy digest, checks the current default-branch commit again at confirmation, and writes a policy PR when needed. For a new migration on the default branch, it adds the policy file to the migration PR. When the branches differ, it creates or refreshes the policy PR before the migration PR and shows both links, with the policy PR first in the merge order. A CMS-ready branch can receive a policy-only PR. Repeated setup reuses an open worker-owned PR for the same default-branch revision; a replacement PR closes older worker-owned policy PRs after it exists. Login and refresh must remain on the existing binding store until their repository-policy checks and migration path are implemented and tested.
