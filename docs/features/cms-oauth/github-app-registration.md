<!-- ai-agent-toolkit:managed version="1.0.0" -->
<!-- Audience: Operators registering or reviewing a GitHub App for this worker.
     Purpose: Define the versioned, least-privilege App settings the worker requires. -->

# GitHub App Registration

## Checklist Version 1

Register separate GitHub Apps for the test and production worker environments. They must have
different client IDs and client secrets; a test App must never authorize the production worker.

Create the App only after its worker has a stable public base URL. For each environment, replace
`<WORKER_BASE_URL>` below with that exact HTTPS origin.

| Setting                                                | Required value                                   |
|--------------------------------------------------------|--------------------------------------------------|
| Homepage URL                                           | The public worker or project homepage URL.       |
| Callback URL                                           | `<WORKER_BASE_URL>/callback`                     |
| Expire user authorization tokens                       | Enabled.                                         |
| Request user authorization (OAuth) during installation | Disabled.                                        |
| Device flow                                            | Disabled.                                        |
| Setup URL                                              | `<WORKER_BASE_URL>/setup`                        |
| Redirect on update                                     | Enabled.                                         |
| Webhook                                                | Inactive. Do not supply a webhook URL or secret. |
| Repository access                                      | Only select repositories.                        |

The worker uses the normal web authorization flow after its popup handshake. Do not enable OAuth during installation: GitHub makes the Setup URL unavailable when that setting is enabled. The setup flow is required both after a new installation and after repositories are added or removed from an existing installation.

Keep expiring user authorization tokens enabled. The worker's refresh endpoint requires GitHub's
refresh token response, which is issued only for expiring GitHub App user tokens.

## Permissions

Grant only these repository permissions:

| Permission    | Access         | Why                                                        |
|---------------|----------------|------------------------------------------------------------|
| Contents      | Read and write | Decap CMS creates and updates comic content.               |
| Pull requests | Read and write | Supports CMS workflows that publish through pull requests. |

Set every other repository, organization, and account permission to **No access**. Subscribe to no
webhook events. Review every future requested permission as a security change: GitHub requires
installation or user approval for permission additions, and the worker must not grow broad access by
accident.

## Setup and Update Verification

1. Install the App for one explicitly selected test repository.
2. After CMS enablement is implemented, confirm GitHub redirects to `<WORKER_BASE_URL>/setup?installation_id=...` and the worker presents CMS enablement guidance rather than an empty page.
3. Complete fresh user authorization, select the verified repository, review the migration summary, and explicitly create the generated pull request.
4. Change the installation's repository selection, then confirm GitHub redirects to the Setup URL again because **Redirect on update** is enabled.
5. Confirm the worker rejects a manually altered `installation_id` unless the newly authorized user token proves access to that installation and selected repository.

GitHub treats `installation_id` in a setup redirect as untrusted browser input. The worker's
verification step is mandatory even when GitHub initiated the redirect.

## Policy and Visibility

| Deployment               | GitHub App visibility                                     | Required worker policy                                                                                |
|--------------------------|-----------------------------------------------------------|-------------------------------------------------------------------------------------------------------|
| comic_git shared service | Public App, operated by comic_git.                        | `ACCESS_POLICY=public` is permitted only here.                                                        |
| Independent deployment   | Chosen by that operator for their users and repositories. | `ACCESS_POLICY=github_login_whitelist` with a non-empty `GITHUB_LOGIN_WHITELIST`. Never use `public`. |

App visibility does not replace the worker access policy. Even a private App needs the whitelist for
an independent deployment, and even the shared App still requires a verified repository/origin
binding before it returns a token.

## Secret Handling

Record each App's client ID in the appropriate non-secret deployment configuration. Generate a
client secret and place it only in the matching environment's Secret Manager or Pulumi secret
configuration. Never commit, log, paste into a registration checklist, or reuse a client secret
between test and production.

See GitHub's [setup URL guidance](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/about-the-setup-url), [App registration settings](https://docs.github.com/en/apps/creating-github-apps/registering-a-github-app/registering-a-github-app), and [refresh-token guidance](https://docs.github.com/en/apps/creating-github-apps/authenticating-with-a-github-app/refreshing-user-access-tokens) when GitHub changes its registration UI.
