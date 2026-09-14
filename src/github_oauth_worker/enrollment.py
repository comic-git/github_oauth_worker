"""Minimal explicit-confirmation page for self-service repository enrollment."""

from github_oauth_worker.github_client import GitHubRepository
from github_oauth_worker.html_templates import render_template


def render_enrollment_confirmation_page(
    origin: str,
    state_token: str,
    choices: tuple[tuple[int, GitHubRepository], ...],
) -> str:
    """Render verified repository choices without embedding GitHub credentials or user sessions."""
    return render_template(
        "enrollment_confirmation.tpl",
        origin=origin,
        state_token=state_token,
        choices=choices,
    )
