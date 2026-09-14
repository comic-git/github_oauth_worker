"""Minimal explicit-confirmation page for self-service repository enrollment."""

import html

from github_oauth_worker.github_client import GitHubRepository


def render_enrollment_confirmation_page(
    origin: str,
    state_token: str,
    choices: tuple[tuple[int, GitHubRepository], ...],
) -> str:
    """Render verified repository choices without embedding GitHub credentials or user sessions."""
    escaped_origin = html.escape(origin)
    escaped_state = html.escape(state_token, quote=True)
    choice_inputs = "".join(
        _choice_input(installation_id, repository) for installation_id, repository in choices
    )
    return f"""<!doctype html>
<html lang="en"><head><meta charset="utf-8"><title>Connect CMS repository</title></head>
<body>
<main><h1>Connect CMS repository</h1><p>{escaped_origin}</p>
<form action="/enroll/select" method="post">
<input type="hidden" name="state" value="{escaped_state}">
{choice_inputs}
<button type="submit">Connect repository</button>
</form></main>
</body></html>"""


def _choice_input(installation_id: int, repository: GitHubRepository) -> str:
    """Render one verified repository option using only escaped display metadata."""
    label = html.escape(f"{repository.owner.login}/{repository.name}")
    return (
        f'<label><input type="radio" name="selection" value="{installation_id}:{repository.id}" '
        f'required> {label}</label><br>'
    )
