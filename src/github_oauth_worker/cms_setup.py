"""Creator-facing pages for direct GitHub App CMS enablement setup."""

from github_oauth_worker.cms_enablement import CmsMigrationPreview, MigrationFileChange
from github_oauth_worker.github_client import (
    GitHubPullRequestDetails,
    GitHubRepository,
    GitHubRepositoryBranch,
)
from github_oauth_worker.html_templates import render_template


def render_cms_setup_start_page(installation_id: int) -> str:
    """Render the direct-install continuation page without treating query data as trusted."""
    return render_template("cms_setup_start.tpl", installation_id=installation_id)


def render_cms_setup_landing_page() -> str:
    """Introduce CMS setup before a creator has installed or selected an App repository."""
    return render_template("cms_setup_landing.tpl")


def render_cms_discovery_page(
    state_token: str,
    repositories: tuple[tuple[int, GitHubRepository], ...],
    github_app_slug: str | None,
) -> str:
    """Show verified installed repositories and a route to grant access to another one."""
    install_url = (
        f"https://github.com/apps/{github_app_slug}/installations/new"
        if github_app_slug is not None
        else None
    )
    return render_template(
        "cms_setup_discovery.tpl",
        state_token=state_token,
        repositories=repositories,
        install_url=install_url,
    )


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


def render_cms_branch_selection_page(
    state_token: str,
    repository: GitHubRepository,
    branches: tuple[GitHubRepositoryBranch, ...],
) -> str:
    """Render branches GitHub exposed after the creator's repository access was reverified."""
    return render_template(
        "cms_setup_branch_selection.tpl",
        state_token=state_token,
        repository=repository,
        branches=branches,
    )


def render_cms_migration_summary_page(
    state_token: str,
    repository: GitHubRepository,
    preview: CmsMigrationPreview,
    changes: tuple[MigrationFileChange, ...],
    existing_pull_request_url: str | None = None,
    up_to_date: bool = False,
    superseded_pull_requests: tuple[GitHubPullRequestDetails, ...] = (),
) -> str:
    """Render a content-free engine migration summary before the creator can request a write."""
    return render_template(
        "cms_setup_summary.tpl",
        state_token=state_token,
        repository=repository,
        preview=preview,
        changes=changes,
        existing_pull_request_url=existing_pull_request_url,
        up_to_date=up_to_date,
        superseded_pull_requests=superseded_pull_requests,
    )


def render_cms_migration_result_page(
    pull_request_url: str, unremoved_branches: tuple[str, ...] = ()
) -> str:
    """Render the final non-secret link to the creator's migration pull request."""
    return render_template(
        "cms_setup_result.tpl",
        pull_request_url=pull_request_url,
        unremoved_branches=unremoved_branches,
    )
