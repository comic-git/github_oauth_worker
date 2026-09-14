<!-- ai-agent-toolkit:managed version="1.0.0" -->
<!-- Audience: AI agents and new team members. -->

# Glossary

**Access policy** - The deployer-controlled rule that decides whether an authenticated GitHub login may use this worker.

**Decap CMS** - The browser-based content editor that comic_git configures for repository-backed comic editing.

**GitHub App** - A GitHub integration with scoped permissions and explicit repository installation. This worker uses its user-authorization flow instead of a broad OAuth App.

**GitHub App user access token** - A short-lived token granted after an editor authorizes the installed GitHub App. Decap uses it directly to call GitHub.

**Manifest** - A versioned JSON definition used by a GitHub owner to register an App with the intended permissions and callback URLs.

**OAuth worker** - This service. It translates Decap's popup protocol into GitHub App authorization and token refresh operations.

**`site_id`** - The site hostname Decap includes in its worker request. It is not a GitHub repository identifier.
