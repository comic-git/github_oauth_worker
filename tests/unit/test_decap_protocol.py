"""Tests for Decap popup handshake and exact-origin callback-page rendering."""

from pydantic import SecretStr

from github_oauth_worker.decap_protocol import (
    DecapTokenPayload,
    render_error_callback_page,
    render_handshake_page,
    render_management_result_page,
    render_origin_completion_handshake_page,
    render_origin_migration_handshake_page,
    render_success_callback_page,
)


def test_handshake_page_captures_only_the_opener_message_origin() -> None:
    page = render_handshake_page()

    assert "event.source !== window.opener" in page
    assert "origin: event.origin" in page
    assert "window.location.search" not in page
    assert 'opener.postMessage(message, "*")' in page


def test_success_callback_targets_only_the_canonical_bound_origin() -> None:
    page = render_success_callback_page(
        "https://CMS.example.com/",
        DecapTokenPayload(
            access_token=SecretStr("user-access-token"),
            token_type="bearer",
            refresh_token=SecretStr("refresh-token"),
            expires_in=28_800,
        ),
    )

    assert 'const targetOrigin = "https://cms.example.com"' in page
    assert "opener.postMessage(message, targetOrigin)" in page
    assert "authorization:github:success:" in page
    assert "user-access-token" in page
    assert "postMessage(message, \"*\")" not in page


def test_error_callback_keeps_the_exact_origin_and_escapes_script_data() -> None:
    page = render_error_callback_page("https://cms.example.com", "<script>unexpected</script>")

    assert 'const targetOrigin = "https://cms.example.com"' in page
    assert "\\u003cscript\\u003eunexpected\\u003c/script\\u003e" in page
    assert "authorization:github:error:" in page


def test_management_templates_escape_text_without_manual_escaping() -> None:
    migration_page = render_origin_migration_handshake_page("https://new-cms.example.com")
    completion_page = render_origin_completion_handshake_page()
    result_page = render_management_result_page(
        "<script>unexpected</script>",
        heading="CMS setup could not continue",
        diagnostic_code="engine_version_too_old",
    )

    assert 'const endpoint = "/origins/migrate/handshake"' in migration_page
    assert 'target_origin: "https://new-cms.example.com"' in migration_page
    assert 'const endpoint = "/origins/complete/handshake"' in completion_page
    assert "target_origin:" not in completion_page
    assert "CMS setup could not continue" in result_page
    assert "engine_version_too_old" in result_page
    assert "&lt;script&gt;unexpected&lt;/script&gt;" in result_page
    assert "<script>unexpected</script>" not in result_page
