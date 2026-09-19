"""Creator-facing pages for direct GitHub App CMS enablement setup."""

from github_oauth_worker.cms_enablement import CmsMigrationPreview
from github_oauth_worker.github_client import GitHubRepository
from github_oauth_worker.html_templates import render_template


def render_cms_setup_start_page(installation_id: int) -> str:
    """Render the direct-install continuation page without treating query data as trusted."""
    return render_template("cms_setup_start.tpl", installation_id=installation_id)


def render_cms_repository_selection_page(
    state_token: str,
    installation_id: int,
    repositories: tuple[GitHubRepository, ...],
) -> str:
    """Render only repositories freshly verified for the installation in direct App setup."""
    return render_template(
        "cms_setup_repository_selection.tpl",
        state_token=state_token,
        installation_id=installation_id,
        repositories=repositories,
    )


def render_cms_migration_summary_page(
    state_token: str,
    repository: GitHubRepository,
    preview: CmsMigrationPreview,
) -> str:
    """Render a content-free engine migration summary before the creator can request a write."""
    return render_template(
        "cms_setup_summary.tpl",
        state_token=state_token,
        repository=repository,
        preview=preview,
    )


def render_cms_migration_result_page(pull_request_url: str) -> str:
    """Render the final non-secret link to the creator's migration pull request."""
    return render_template("cms_setup_result.tpl", pull_request_url=pull_request_url)
