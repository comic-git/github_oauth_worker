<!-- ai-agent-toolkit:managed version="1.0.0" -->
<!-- Audience: AI agents and developers diagnosing failures. -->

# Debugging

## Current State

There is no running service or log configuration until implementation begins. The diagnostics below apply after a Cloud Run deployment exists.

## Reading Logs

```powershell
gcloud run services logs read github-oauth-worker-<environment> --region <region> --project <project-id>
gcloud run services logs read github-oauth-worker-<environment> --region <region> --project <project-id> --log-filter='severity>=ERROR'
```

Logs may contain request IDs, endpoint names, status codes, and safe failure categories. They must not contain authorization codes, state, cookies, GitHub client secrets, access tokens, or refresh tokens.

## Common Failures

| Symptom                                   | Likely cause                                                                                                          | Check                                                                          |
|-------------------------------------------|-----------------------------------------------------------------------------------------------------------------------|--------------------------------------------------------------------------------|
| GitHub rejects the callback               | Registered App callback URL differs from the deployed public URL                                                      | Compare the exact scheme, host, and path.                                      |
| Decap popup does not finish               | Worker URL or callback message format is incorrect                                                                    | Confirm Decap's configured base URL and inspect the popup's browser console.   |
| User is denied after GitHub authorization | Login policy denied enrollment, no active origin binding exists, or the App is not installed for the bound repository | Check the whitelist, binding status, and App installation.                     |
| Refresh fails                             | Token expired, refresh token is invalid, policy changed, or the bound repository is no longer selected for the App    | Check the safe failure category and re-authenticate without logging the token. |

## Useful Diagnostics

### CMS Setup Logs

CMS setup uses Google's `StructuredLogHandler` to write Cloud Logging JSON records to stdout. They
include the safe repository identity, the main config path, the declared engine selector, the
official engine ref requested, resolved engine commit SHA, and the migration PR outcome. Search
Cloud Run logs for `jsonPayload.event:"cms_setup_"` to follow one setup attempt.

Expected setup failures include a stable `diagnostic_code` in the `oauth_callback_failed` event and
on the administrator-facing result page. The code identifies the safe failure category; it never
contains OAuth state, authorization codes, access tokens, or migration file contents.

```powershell
curl https://<worker-host>/healthz
gcloud run services describe github-oauth-worker-<environment> --region <region> --project <project-id>
```

Use a dedicated sandbox App and repository for real browser troubleshooting. Never reproduce an issue by copying a production token into a terminal command or bug report.
