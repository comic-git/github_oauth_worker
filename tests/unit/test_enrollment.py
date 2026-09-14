"""Tests for explicit repository-enrollment confirmation rendering."""

from github_oauth_worker.enrollment import render_enrollment_confirmation_page
from github_oauth_worker.github_client import GitHubRepository, GitHubRepositoryOwner


def test_enrollment_confirmation_escapes_untrusted_display_values() -> None:
    page = render_enrollment_confirmation_page(
        "https://cms.example.com/<script>",
        'state"><script>unexpected</script>',
        (
            (
                456,
                GitHubRepository(
                    id=123,
                    name="comic<script>",
                    owner=GitHubRepositoryOwner(login="owner<script>"),
                ),
            ),
        ),
    )

    assert "&lt;script&gt;" in page
    assert '<script>unexpected</script>' not in page
    assert 'value="state&#34;&gt;&lt;script&gt;unexpected&lt;/script&gt;"' in page
