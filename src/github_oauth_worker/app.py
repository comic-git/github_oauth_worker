"""FastAPI application composition for the GitHub OAuth worker."""

import hashlib
import json
import logging
import re
from collections.abc import Awaitable, Callable
from dataclasses import replace
from urllib.parse import parse_qs, urlencode

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, Field, SecretStr, ValidationError

from github_oauth_worker.cms_access import (
    CMS_ACCESS_POLICY_PATH,
    CmsAccessPolicyChange,
    CmsAccessPolicyError,
    CmsAccessPolicyPlan,
    CmsAccessPolicyPlanError,
    load_cms_access_policy,
    parse_cms_access_policy,
    plan_cms_access_policy,
)
from github_oauth_worker.cms_access_delivery import (
    current_policy_pull_request,
    deliver_cms_access_policy,
)
from github_oauth_worker.cms_enablement import (
    CmsMigrationPreview,
    MigrationPlanFile,
    TargetRepositoryState,
    build_migration_preview,
    compare_migration_files,
    migration_branch_name,
    read_target_repository_state,
    resolve_official_engine_commit,
)
from github_oauth_worker.cms_setup import (
    render_cms_access_origins_page,
    render_cms_branch_selection_page,
    render_cms_discovery_page,
    render_cms_migration_result_page,
    render_cms_migration_summary_page,
    render_cms_repository_selection_page,
    render_cms_setup_landing_page,
    render_cms_setup_start_page,
)
from github_oauth_worker.config import WorkerSettings, load_settings
from github_oauth_worker.decap_protocol import (
    DecapTokenPayload,
    render_error_callback_page,
    render_handshake_page,
    render_management_result_page,
    render_success_callback_page,
)
from github_oauth_worker.engine_resolution import EngineSelectorError, EngineSelectorPolicy
from github_oauth_worker.errors import WorkerError
from github_oauth_worker.github_client import (
    GitHubAccessVerificationError,
    GitHubAppClient,
    GitHubClientError,
    GitHubGitReference,
    GitHubGitTree,
    GitHubPullRequestDetails,
    GitHubRepository,
    GitHubTreeChange,
)
from github_oauth_worker.logging import configure_event_logger, log_event
from github_oauth_worker.origin import canonicalize_origin
from github_oauth_worker.policy import AccessOperation, build_access_policy
from github_oauth_worker.setup_session import (
    InvalidSetupSessionError,
    SetupSession,
    SetupSessionManager,
)
from github_oauth_worker.state import IssuedOAuthState, OAuthFlow, OAuthState, OAuthStateManager


class AuthHandshakeRequest(BaseModel):
    """The origin received by the popup page from Decap's opener-message event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    origin: str


class RefreshTokenRequest(BaseModel):
    """The only Decap refresh body field needed by the worker."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    refresh_token: SecretStr


class CmsMigrationReviewChangedError(WorkerError):
    """Require a new review when the target or generated migration plan changes."""

    status_code = 409
    diagnostic_code = "cms_migration_review_changed"
    public_message = (
        "The target branch or migration plan changed. Review the latest result and try again."
    )


class CmsMigrationRetryError(WorkerError):
    """A GitHub write raced with another change; a fresh review is enough to recover."""

    status_code = 409
    diagnostic_code = "cms_migration_retry"
    public_message = (
        "The repository changed while CMS setup was saving. Review the latest result and try again."
    )


class CmsSetupCancelledError(WorkerError):
    """A creator may restart setup after leaving GitHub's authorization screen."""

    diagnostic_code = "cms_setup_authorization_cancelled"
    public_message = "GitHub authorization was not completed. Start CMS setup again when ready."


class CmsSetupSelectionRequest(BaseModel):
    """A signed direct-setup state and repository chosen from its verified installation list."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: str
    repository_id: int


class CmsSetupDiscoverySelectionRequest(BaseModel):
    """A signed discovery state and an installation/repository pair shown by GitHub."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: str
    selection: str = Field(pattern=r"^[1-9][0-9]*:[1-9][0-9]*$")


class CmsSetupConfirmationRequest(BaseModel):
    """A signed reviewed migration summary requiring final GitHub access checks."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: str


class CmsSetupBranchSelectionRequest(BaseModel):
    """A signed repository-selection state and the branch chosen from GitHub's verified list."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: str
    target_branch: str


class CmsSetupAccessReviewRequest(BaseModel):
    """Creator-confirmed HTTPS origins submitted from a signed branch selection."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: str
    approved_origins: str = Field(min_length=1, max_length=4096)


async def _validated_form[FormModel: BaseModel](
    request: Request, model: type[FormModel]
) -> FormModel:
    """Validate a browser form without adding a multipart parser dependency."""
    try:
        fields = parse_qs((await request.body()).decode("utf-8"))
        return model.model_validate({name: values[0] for name, values in fields.items() if values})
    except UnicodeDecodeError, ValidationError:
        raise WorkerError from None


_CMS_ENABLEMENT_FLOWS = frozenset(
    {
        OAuthFlow.CMS_ENABLEMENT_DISCOVERY,
        OAuthFlow.CMS_ENABLEMENT_INSTALLATION,
        OAuthFlow.CMS_ENABLEMENT_REPOSITORY,
        OAuthFlow.CMS_ENABLEMENT_BRANCH,
        OAuthFlow.CMS_ENABLEMENT_CONFIRMATION,
    }
)


def create_app(
    settings: WorkerSettings | None = None,
    github_client: GitHubAppClient | None = None,
) -> FastAPI:
    """Create the worker and compose ready-mode dependencies only after settings validation."""
    worker_settings = settings or load_settings()
    logger = configure_event_logger(
        project_id=worker_settings.gcp_project_id,
        labels={
            "product": "github_oauth_worker",
            "service": "github-oauth-worker",
            "environment": worker_settings.environment.value,
        },
    )
    app = FastAPI(
        title="github_oauth_worker",
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )
    app.state.settings = worker_settings

    if worker_settings.service_mode.value == "ready":

        async def run_setup_session_step(
            request: Request,
            form_state: OAuthState,
            next_state: OAuthState,
            handler: Callable[[OAuthState, SetupSession], Awaitable[HTMLResponse]],
            *,
            clear_on_success: bool = False,
        ) -> HTMLResponse | None:
            """Use one bounded setup credential across forms and keep errors browser-readable."""
            try:
                session = app.state.setup_sessions.read(
                    request.cookies.get(SetupSessionManager.COOKIE_NAME),
                    form_state.nonce,
                    expected_installation_id=form_state.installation_id,
                    expected_repository_id=form_state.repository_id,
                )
                if session is None:
                    return None
                response = await handler(next_state, session)
                if clear_on_success:
                    app.state.setup_sessions.clear(response)
                return response
            except WorkerError as error:
                log_event(
                    logger,
                    "cms_setup_session_step_failed",
                    repository_id=next_state.repository_id,
                    error_type=type(error).__name__,
                    diagnostic_code=error.diagnostic_code,
                    status_code=error.status_code,
                    **error.safe_log_fields(),
                )
                return HTMLResponse(
                    render_management_result_page(
                        error.public_message,
                        heading="comic_git CMS setup could not continue",
                        diagnostic_code=error.diagnostic_code,
                        retry_url="/setup",
                        retry_label="Start CMS setup again",
                    ),
                    status_code=error.status_code,
                )
            except Exception as error:
                log_event(
                    logger,
                    "cms_setup_session_step_unexpected_failure",
                    severity=logging.ERROR,
                    repository_id=next_state.repository_id,
                    error_type=type(error).__name__,
                )
                return HTMLResponse(
                    render_management_result_page(
                        WorkerError.public_message,
                        heading="comic_git CMS setup could not continue",
                        diagnostic_code="unexpected_setup_failure",
                        retry_url="/setup",
                        retry_label="Start CMS setup again",
                    ),
                    status_code=500,
                )

        def redirect_to_github(
            issued_state: IssuedOAuthState,
            repository_id: int | None = None,
            *,
            select_account: bool = False,
        ) -> RedirectResponse:
            """Redirect a cookie-bound flow to GitHub without query origin identity."""
            callback_url = f"{str(worker_settings.public_base_url).rstrip('/')}/callback"
            parameters = {
                "client_id": worker_settings.github_app_client_id,
                "redirect_uri": callback_url,
                "state": issued_state.token,
            }
            if repository_id is not None:
                parameters["repository_id"] = str(repository_id)
            if select_account:
                parameters["prompt"] = "select_account"
            response = RedirectResponse(
                "https://github.com/login/oauth/authorize?" + urlencode(parameters),
                status_code=302,
            )
            app.state.state_manager.attach_correlation_cookie(response, issued_state)
            return response

        async def authorize_cms_origin(
            user_access_token: SecretStr,
            repository: GitHubRepository,
            origin: str,
        ) -> None:
            """Read live default-branch approval immediately before releasing a token."""
            content = await app.state.github_client.get_default_branch_cms_access_policy(
                user_access_token, repository
            )
            if content is None:
                raise CmsAccessPolicyError
            policy = parse_cms_access_policy(
                content,
                repository_id=repository.id,
                worker_origin=str(worker_settings.public_base_url).rstrip("/"),
            )
            if not policy.authorizes_origin(origin):
                raise CmsAccessPolicyError

        app.state.github_client = github_client or GitHubAppClient.from_settings(worker_settings)
        app.state.state_manager = OAuthStateManager.from_settings(worker_settings)
        app.state.setup_sessions = SetupSessionManager.from_settings(worker_settings)

    @app.exception_handler(WorkerError)
    async def handle_worker_error(request: Request, error: WorkerError) -> JSONResponse:
        """Return only the deliberate public error text for expected worker failures."""
        headers = None
        if re.fullmatch(r"/auth/[1-9][0-9]*/refresh", request.url.path):
            try:
                headers = _cors_headers(_require_bound_origin(request))
            except WorkerError:
                pass
        return JSONResponse(
            status_code=error.status_code,
            content={"error": error.public_message},
            headers=headers,
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        """Report readiness without exposing configuration or secret state."""
        return {"status": worker_settings.service_mode.value}

    if worker_settings.service_mode.value == "ready":

        @app.get("/auth/{repository_id}")
        def auth(repository_id: int) -> HTMLResponse:
            """Capture Decap's actual opener origin for a repository selected by its numeric ID."""
            if repository_id <= 0:
                raise WorkerError
            return HTMLResponse(render_handshake_page(f"/auth/{repository_id}/handshake"))

        @app.get("/setup")
        def setup(installation_id: int | None = None) -> HTMLResponse:
            """Start guided setup or receive an untrusted GitHub App installation redirect."""
            if installation_id is None:
                return HTMLResponse(render_cms_setup_landing_page())
            if installation_id <= 0:
                raise WorkerError
            return HTMLResponse(render_cms_setup_start_page(installation_id))

        @app.post("/setup/start")
        def setup_start() -> RedirectResponse:
            """Authorize the creator before discovering their App-visible repositories."""
            return redirect_to_github(
                app.state.state_manager.issue(OAuthFlow.CMS_ENABLEMENT_DISCOVERY)
            )

        @app.post("/setup/continue")
        async def setup_continue(request: Request) -> Response:
            """Verify GitHub's installation redirect with the discovery token when possible."""
            try:
                form_data = parse_qs((await request.body()).decode("utf-8"))
                installation_id = int(form_data["installation_id"][0])
                if installation_id <= 0:
                    raise ValueError
            except KeyError, UnicodeDecodeError, ValueError:
                raise WorkerError from None

            discovery_nonce = request.cookies.get(
                app.state.state_manager.correlation_cookie_name(OAuthFlow.CMS_ENABLEMENT_DISCOVERY)
            )
            if discovery_nonce is not None:
                try:
                    session = app.state.setup_sessions.read(
                        request.cookies.get(SetupSessionManager.COOKIE_NAME),
                        discovery_nonce,
                        expected_scope="discovery",
                    )
                    if session is not None:
                        issued_state = app.state.state_manager.issue_cms_enablement_installation(
                            installation_id
                        )
                        oauth_state = app.state.state_manager.consume(
                            issued_state.token,
                            issued_state.correlation_nonce,
                            OAuthFlow.CMS_ENABLEMENT_INSTALLATION,
                        )
                        response = await cms_enablement_installation_callback(
                            oauth_state, session.access_token, issued_state.token
                        )
                        app.state.state_manager.attach_correlation_cookie(response, issued_state)
                        log_event(
                            logger,
                            "cms_setup_discovery_authorization_reused",
                            installation_id=installation_id,
                        )
                        return response
                except WorkerError as error:
                    log_event(
                        logger,
                        "cms_setup_installation_reauthorization_required",
                        installation_id=installation_id,
                        error_type=type(error).__name__,
                    )
            return redirect_to_github(
                app.state.state_manager.issue_cms_enablement_installation(installation_id)
            )

        @app.post("/setup/switch-account")
        def setup_switch_account() -> RedirectResponse:
            """Let the creator choose another GitHub identity and restart discovery."""
            response = redirect_to_github(
                app.state.state_manager.issue(OAuthFlow.CMS_ENABLEMENT_DISCOVERY),
                select_account=True,
            )
            app.state.setup_sessions.clear(response)
            return response

        async def continue_selected_repository(
            request: Request,
            form_state: OAuthState,
            installation_id: int,
            repository_id: int,
        ) -> Response:
            """Verify a selected repo with the brief discovery token, or reauthorize."""
            issued_state = app.state.state_manager.issue_cms_enablement_repository(
                repository_id, installation_id
            )
            try:
                session = app.state.setup_sessions.read(
                    request.cookies.get(SetupSessionManager.COOKIE_NAME),
                    form_state.nonce,
                    expected_scope="discovery",
                )
            except InvalidSetupSessionError:
                session = None
                log_event(
                    logger,
                    "cms_setup_repository_reauthorization_required",
                    repository_id=repository_id,
                    installation_id=installation_id,
                    reason="invalid_discovery_session",
                )
            if session is not None:
                repository_state = app.state.state_manager.consume(
                    issued_state.token,
                    issued_state.correlation_nonce,
                    OAuthFlow.CMS_ENABLEMENT_REPOSITORY,
                )
                try:
                    response = await cms_enablement_repository_callback(repository_state, session)
                except (GitHubClientError, GitHubAccessVerificationError) as error:
                    log_event(
                        logger,
                        "cms_setup_repository_reauthorization_required",
                        repository_id=repository_id,
                        installation_id=installation_id,
                        error_type=type(error).__name__,
                    )
                except WorkerError as error:
                    log_event(
                        logger,
                        "cms_setup_repository_selection_failed",
                        repository_id=repository_id,
                        installation_id=installation_id,
                        error_type=type(error).__name__,
                        diagnostic_code=error.diagnostic_code,
                    )
                    return HTMLResponse(
                        render_management_result_page(
                            error.public_message,
                            heading="comic_git CMS setup could not continue",
                            diagnostic_code=error.diagnostic_code,
                            retry_url="/setup",
                            retry_label="Start CMS setup again",
                        ),
                        status_code=error.status_code,
                    )
                else:
                    log_event(
                        logger,
                        "cms_setup_discovery_authorization_reused_for_repository",
                        repository_id=repository_id,
                        installation_id=installation_id,
                    )
                    return response
            elif request.cookies.get(SetupSessionManager.COOKIE_NAME) is None:
                log_event(
                    logger,
                    "cms_setup_repository_reauthorization_required",
                    repository_id=repository_id,
                    installation_id=installation_id,
                    reason="missing_discovery_session",
                )
            return redirect_to_github(issued_state, repository_id)

        @app.post("/setup/select")
        async def setup_select(request: Request) -> Response:
            """Verify the selected repository after GitHub's installation redirect."""
            try:
                form_data = parse_qs((await request.body()).decode("utf-8"))
                selection = CmsSetupSelectionRequest.model_validate(
                    {name: values[0] for name, values in form_data.items() if values}
                )
                oauth_state = app.state.state_manager.consume_from_cookies(
                    selection.state, request.cookies
                )
                if oauth_state.flow is not OAuthFlow.CMS_ENABLEMENT_INSTALLATION:
                    raise ValueError
                assert oauth_state.installation_id is not None
            except UnicodeDecodeError, ValidationError, ValueError:
                raise WorkerError from None
            return await continue_selected_repository(
                request, oauth_state, oauth_state.installation_id, selection.repository_id
            )

        @app.post("/setup/select-discovered")
        async def setup_select_discovered(request: Request) -> Response:
            """Verify the chosen App repository using the discovery session when possible."""
            selection = await _validated_form(request, CmsSetupDiscoverySelectionRequest)
            oauth_state = app.state.state_manager.consume_from_cookies(
                selection.state, request.cookies
            )
            if oauth_state.flow is not OAuthFlow.CMS_ENABLEMENT_DISCOVERY:
                raise WorkerError
            installation_text, repository_text = selection.selection.split(":")
            installation_id = int(installation_text)
            repository_id = int(repository_text)
            return await continue_selected_repository(
                request, oauth_state, installation_id, repository_id
            )

        @app.post("/setup/confirm")
        async def setup_confirm(request: Request) -> Response:
            """Recheck the exact reviewed plan with its repository-bound setup session."""
            try:
                form_data = parse_qs((await request.body()).decode("utf-8"))
                confirmation = CmsSetupConfirmationRequest.model_validate(
                    {name: values[0] for name, values in form_data.items() if values}
                )
                oauth_state = app.state.state_manager.consume_from_cookies(
                    confirmation.state, request.cookies
                )
                if oauth_state.flow is not OAuthFlow.CMS_ENABLEMENT_CONFIRMATION:
                    raise ValueError
                assert oauth_state.repository_id is not None
            except UnicodeDecodeError, ValidationError, ValueError:
                raise WorkerError from None
            assert oauth_state.installation_id is not None
            assert oauth_state.target_branch is not None
            assert oauth_state.base_commit_sha is not None
            assert oauth_state.engine_selector is not None
            assert oauth_state.engine_commit_sha is not None
            assert oauth_state.reviewed_plan_digest is not None
            issued_state = app.state.state_manager.issue_cms_enablement_confirmation(
                oauth_state.repository_id,
                oauth_state.installation_id,
                oauth_state.target_branch,
                oauth_state.base_commit_sha,
                oauth_state.engine_selector,
                oauth_state.engine_commit_sha,
                oauth_state.reviewed_plan_digest,
                approved_origins=oauth_state.approved_origins,
                default_branch_commit_sha=oauth_state.default_branch_commit_sha,
                reviewed_policy_digest=oauth_state.reviewed_policy_digest,
            )
            confirmed_state = app.state.state_manager.consume(
                issued_state.token,
                issued_state.correlation_nonce,
                OAuthFlow.CMS_ENABLEMENT_CONFIRMATION,
            )
            response = await run_setup_session_step(
                request,
                oauth_state,
                confirmed_state,
                cms_enablement_confirmation_callback,
                clear_on_success=True,
            )
            if response is not None:
                return response
            return redirect_to_github(issued_state, oauth_state.repository_id)

        @app.post("/setup/select-branch")
        async def setup_select_branch(request: Request) -> Response:
            """Plan the selected branch using a verified setup session or fresh authorization."""
            try:
                form_data = parse_qs((await request.body()).decode("utf-8"))
                selection = CmsSetupBranchSelectionRequest.model_validate(
                    {name: values[0] for name, values in form_data.items() if values}
                )
                oauth_state = app.state.state_manager.consume_from_cookies(
                    selection.state, request.cookies
                )
                if oauth_state.flow is not OAuthFlow.CMS_ENABLEMENT_REPOSITORY:
                    raise ValueError
                assert oauth_state.repository_id is not None
                assert oauth_state.installation_id is not None
            except UnicodeDecodeError, ValidationError, ValueError:
                raise WorkerError from None
            issued_state = app.state.state_manager.issue_cms_enablement_branch(
                oauth_state.repository_id,
                oauth_state.installation_id,
                selection.target_branch,
            )
            branch_state = app.state.state_manager.consume(
                issued_state.token,
                issued_state.correlation_nonce,
                OAuthFlow.CMS_ENABLEMENT_BRANCH,
            )
            response = await run_setup_session_step(
                request, oauth_state, branch_state, cms_enablement_branch_callback
            )
            if response is not None:
                return response
            return redirect_to_github(issued_state, oauth_state.repository_id)

        @app.post("/setup/review-access")
        async def setup_review_access(request: Request) -> Response:
            """Bind the creator's explicit website approvals to the signed CMS review."""
            selection = await _validated_form(request, CmsSetupAccessReviewRequest)
            oauth_state = app.state.state_manager.consume_from_cookies(
                selection.state, request.cookies
            )
            if oauth_state.flow is not OAuthFlow.CMS_ENABLEMENT_BRANCH:
                raise WorkerError
            assert oauth_state.repository_id is not None
            assert oauth_state.installation_id is not None
            assert oauth_state.target_branch is not None
            try:
                approved_origins = tuple(
                    canonicalize_origin(line.strip())
                    for line in selection.approved_origins.splitlines()
                    if line.strip()
                )
                if not approved_origins:
                    raise ValueError
                issued_state = app.state.state_manager.issue_cms_enablement_branch(
                    oauth_state.repository_id,
                    oauth_state.installation_id,
                    oauth_state.target_branch,
                    correlation_nonce=oauth_state.nonce,
                    approved_origins=approved_origins,
                )
            except ValueError:
                error = CmsAccessPolicyPlanError()
                return HTMLResponse(
                    render_management_result_page(
                        error.public_message,
                        heading="comic_git CMS setup could not continue",
                        diagnostic_code=error.diagnostic_code,
                        retry_url="/setup",
                        retry_label="Start CMS setup again",
                    ),
                    status_code=error.status_code,
                )
            reviewed_state = app.state.state_manager.consume(
                issued_state.token,
                issued_state.correlation_nonce,
                OAuthFlow.CMS_ENABLEMENT_BRANCH,
            )
            response = await run_setup_session_step(
                request, oauth_state, reviewed_state, cms_enablement_branch_callback
            )
            if response is not None:
                return response
            return redirect_to_github(issued_state, oauth_state.repository_id)

        @app.post("/auth/{repository_id}/handshake")
        async def auth_handshake(repository_id: int, request: Request) -> RedirectResponse:
            """Sign the browser-reported origin and repository selector into OAuth state."""
            if repository_id <= 0:
                raise WorkerError
            handshake = await _validated_form(request, AuthHandshakeRequest)
            try:
                origin = canonicalize_origin(handshake.origin)
            except ValueError:
                raise WorkerError from None
            issued_state = app.state.state_manager.issue_decap(origin, repository_id)
            return redirect_to_github(issued_state, repository_id)

        @app.get("/callback")
        async def callback(
            request: Request,
            code: str | None = None,
            state: str | None = None,
        ) -> HTMLResponse:
            """Complete authorization and send a verified token only to the signed CMS origin."""
            state_manager = app.state.state_manager
            oauth_state = state_manager.consume_from_cookies(state, request.cookies)
            try:
                if not code:
                    if oauth_state.flow is OAuthFlow.CMS_ENABLEMENT_DISCOVERY:
                        raise CmsSetupCancelledError
                    raise WorkerError
                if oauth_state.flow is OAuthFlow.CMS_ENABLEMENT_DISCOVERY:
                    return await cms_enablement_discovery_callback(oauth_state, code, state)
                if oauth_state.flow is OAuthFlow.CMS_ENABLEMENT_INSTALLATION:
                    return await cms_enablement_installation_callback(oauth_state, code, state)
                if oauth_state.flow is OAuthFlow.CMS_ENABLEMENT_REPOSITORY:
                    return await cms_enablement_repository_callback(oauth_state, code)
                if oauth_state.flow is OAuthFlow.CMS_ENABLEMENT_BRANCH:
                    return await cms_enablement_branch_callback(oauth_state, code)
                if oauth_state.flow is OAuthFlow.CMS_ENABLEMENT_CONFIRMATION:
                    return await cms_enablement_confirmation_callback(oauth_state, code)
                if oauth_state.flow is not OAuthFlow.DECAP:
                    raise WorkerError
                assert oauth_state.origin is not None
                assert oauth_state.repository_id is not None
                access_token = await app.state.github_client.exchange_authorization_code(
                    code,
                    oauth_state.repository_id,
                )
                github_user = await app.state.github_client.get_authenticated_user(
                    access_token.access_token
                )
                build_access_policy(worker_settings).require_permitted(
                    github_user.login,
                    AccessOperation.AUTHORIZATION,
                )
                repository = await app.state.github_client.verify_repository_access(
                    access_token.access_token,
                    oauth_state.repository_id,
                )
                await authorize_cms_origin(
                    access_token.access_token, repository, oauth_state.origin
                )
                return HTMLResponse(
                    render_success_callback_page(
                        oauth_state.origin,
                        DecapTokenPayload(
                            access_token=access_token.access_token,
                            token_type=access_token.token_type,
                            refresh_token=access_token.refresh_token,
                            expires_in=access_token.expires_in,
                        ),
                    )
                )
            except WorkerError as error:
                log_event(
                    logger,
                    "oauth_callback_failed",
                    flow=oauth_state.flow,
                    repository_id=oauth_state.repository_id,
                    error_type=type(error).__name__,
                    diagnostic_code=error.diagnostic_code,
                    status_code=error.status_code,
                    public_message=error.public_message,
                    **error.safe_log_fields(),
                )
                if oauth_state.flow in _CMS_ENABLEMENT_FLOWS:
                    retry_url = None
                    if isinstance(error, CmsSetupCancelledError):
                        retry_url = "/setup"
                    elif (
                        isinstance(error, (CmsMigrationRetryError, CmsMigrationReviewChangedError))
                        and oauth_state.installation_id is not None
                    ):
                        retry_url = f"/setup?installation_id={oauth_state.installation_id}"
                    return HTMLResponse(
                        render_management_result_page(
                            error.public_message,
                            heading="comic_git CMS setup could not continue",
                            diagnostic_code=error.diagnostic_code,
                            retry_url=retry_url,
                            retry_label=(
                                "Start CMS setup again"
                                if isinstance(error, CmsSetupCancelledError)
                                else "Review latest result"
                            ),
                        ),
                        status_code=error.status_code,
                    )
                return HTMLResponse(
                    render_error_callback_page(oauth_state.origin, error.public_message),
                    status_code=error.status_code,
                )
            except Exception as error:
                log_event(
                    logger,
                    "oauth_callback_unexpected_failure",
                    severity=logging.ERROR,
                    flow=oauth_state.flow,
                    repository_id=oauth_state.repository_id,
                    error_type=type(error).__name__,
                )
                if oauth_state.flow in _CMS_ENABLEMENT_FLOWS:
                    return HTMLResponse(
                        render_management_result_page(
                            WorkerError.public_message,
                            heading="comic_git CMS setup could not continue",
                            diagnostic_code="unexpected_callback_failure",
                        ),
                        status_code=500,
                    )
                return HTMLResponse(
                    render_error_callback_page(oauth_state.origin, WorkerError.public_message),
                    status_code=500,
                )

        async def cms_enablement_installation_callback(
            oauth_state: OAuthState,
            authorization: str | SecretStr,
            state_token: str | None,
        ) -> HTMLResponse:
            """List only repositories proven visible to this authorized GitHub user."""
            assert oauth_state.installation_id is not None
            if isinstance(authorization, SecretStr):
                user_access_token = authorization
            else:
                exchanged_token = await app.state.github_client.exchange_authorization_code(
                    authorization
                )
                user_access_token = exchanged_token.access_token
            github_user = await app.state.github_client.get_authenticated_user(user_access_token)
            build_access_policy(worker_settings).require_permitted(
                github_user.login, AccessOperation.ENROLLMENT
            )
            installations = await app.state.github_client.list_accessible_installations(
                user_access_token
            )
            installation_ids = {installation.id for installation in installations}
            if oauth_state.installation_id not in installation_ids:
                raise WorkerError
            repositories = await app.state.github_client.list_repositories_for_installation(
                user_access_token, oauth_state.installation_id
            )
            if not repositories or state_token is None:
                raise WorkerError
            log_event(
                logger,
                "cms_setup_repository_selection_ready",
                installation_id=oauth_state.installation_id,
                github_login=github_user.login,
                repository_count=len(repositories),
            )
            response = HTMLResponse(
                render_cms_repository_selection_page(
                    state_token, oauth_state.installation_id, repositories
                )
            )
            app.state.setup_sessions.attach(
                response,
                user_access_token,
                oauth_state.nonce,
                scope="discovery",
            )
            return response

        async def cms_enablement_discovery_callback(
            oauth_state: OAuthState,
            code: str,
            state_token: str | None,
        ) -> HTMLResponse:
            """Show repositories the creator can access through existing App installations."""
            if state_token is None:
                raise WorkerError
            access_token = await app.state.github_client.exchange_authorization_code(code)
            github_user = await app.state.github_client.get_authenticated_user(
                access_token.access_token
            )
            build_access_policy(worker_settings).require_permitted(
                github_user.login, AccessOperation.ENROLLMENT
            )
            installations = await app.state.github_client.list_accessible_installations(
                access_token.access_token
            )
            choices = []
            for installation in installations:
                repositories = await app.state.github_client.list_repositories_for_installation(
                    access_token.access_token, installation.id
                )
                choices.extend((installation.id, repository) for repository in repositories)
            log_event(
                logger,
                "cms_setup_discovery_ready",
                github_login=github_user.login,
                installation_count=len(installations),
                repository_count=len(choices),
            )
            response = HTMLResponse(
                render_cms_discovery_page(
                    state_token, tuple(choices), worker_settings.github_app_slug
                )
            )
            app.state.setup_sessions.attach(
                response,
                access_token.access_token,
                oauth_state.nonce,
                scope="discovery",
            )
            return response

        async def build_logged_cms_migration_preview(
            repository: GitHubRepository,
            user_access_token: SecretStr,
            target_branch: str,
        ) -> tuple[TargetRepositoryState, CmsMigrationPreview]:
            """Log non-secret setup milestones around the bounded migration-planning boundary."""
            repository_fields = {
                "repository_id": repository.id,
                "repository_owner": repository.owner.login,
                "repository_name": repository.name,
                "default_branch": repository.default_branch,
                "target_branch": target_branch,
            }
            log_event(logger, "cms_setup_repository_verified", **repository_fields)
            target_state = await read_target_repository_state(
                app.state.github_client, user_access_token, repository, target_branch
            )
            log_event(
                logger,
                "cms_setup_target_state_read",
                **repository_fields,
                base_commit_sha=target_state.base_commit_sha,
                config_path=target_state.config_path,
                declared_engine_selector=target_state.engine_selector,
                tree_entry_count=len(target_state.tree.tree),
            )
            selector_policy = EngineSelectorPolicy.from_settings(worker_settings)
            log_event(
                logger,
                "cms_setup_engine_selector_validation_started",
                **repository_fields,
                declared_engine_selector=target_state.engine_selector,
                minimum_engine_version=worker_settings.cms_minimum_engine_version,
                allowed_engine_branches=sorted(worker_settings.cms_allowed_engine_branches),
            )
            try:
                selector = selector_policy.select(target_state.engine_selector)
            except EngineSelectorError as error:
                log_event(
                    logger,
                    "cms_setup_engine_selector_rejected",
                    **repository_fields,
                    declared_engine_selector=target_state.engine_selector,
                    error_type=type(error).__name__,
                    diagnostic_code=error.diagnostic_code,
                    public_message=error.public_message,
                )
                raise
            log_event(
                logger,
                "cms_setup_engine_source_requested",
                **repository_fields,
                declared_engine_selector=selector.value,
                engine_ref=selector.ref,
                minimum_engine_version=worker_settings.cms_minimum_engine_version,
                allowed_engine_branches=sorted(worker_settings.cms_allowed_engine_branches),
            )
            engine_commit_sha = await resolve_official_engine_commit(
                app.state.github_client, selector
            )
            log_event(
                logger,
                "cms_setup_engine_source_resolved",
                **repository_fields,
                declared_engine_selector=selector.value,
                engine_ref=selector.ref,
                engine_commit_sha=engine_commit_sha,
            )
            preview = await build_migration_preview(
                app.state.github_client,
                user_access_token,
                repository,
                target_state,
                selector,
                engine_commit_sha,
                _cms_enablement_input(worker_settings, repository, target_branch),
            )
            log_event(
                logger,
                "cms_setup_migration_preview_ready",
                **repository_fields,
                base_commit_sha=preview.base_commit_sha,
                engine_selector=preview.engine_selector,
                engine_commit_sha=preview.engine_commit_sha,
                planned_file_count=len(preview.files),
                planned_paths=preview.changed_paths,
            )
            return target_state, preview

        async def build_cms_access_review(
            repository: GitHubRepository,
            access_token: SecretStr,
            target_branch: str,
            approved_origins: tuple[str, ...],
        ) -> tuple[CmsAccessPolicyPlan, str]:
            """Pair the proposed policy with one stable default-branch revision."""
            if repository.default_branch is None:
                raise CmsAccessPolicyPlanError
            branch_ref = f"heads/{repository.default_branch}"
            before = await app.state.github_client.get_repository_ref(
                access_token, repository, branch_ref
            )
            if before.object.type != "commit":
                raise CmsAccessPolicyPlanError
            existing = await app.state.github_client.get_default_branch_cms_access_policy(
                access_token, repository
            )
            after = await app.state.github_client.get_repository_ref(
                access_token, repository, branch_ref
            )
            if before.object.sha != after.object.sha:
                raise CmsMigrationReviewChangedError
            plan = plan_cms_access_policy(
                existing_content=existing,
                repository_id=repository.id,
                default_branch=repository.default_branch,
                target_branch=target_branch,
                worker_origin=str(worker_settings.public_base_url),
                approved_origins=approved_origins,
            )
            return plan, before.object.sha

        def include_cms_access_policy(
            preview: CmsMigrationPreview, plan: CmsAccessPolicyPlan
        ) -> CmsMigrationPreview:
            """Add the policy once, without overriding an engine-owned migration file."""
            if CMS_ACCESS_POLICY_PATH in preview.changed_paths or any(
                file.path == CMS_ACCESS_POLICY_PATH for file in preview.files
            ):
                raise CmsAccessPolicyPlanError
            return replace(
                preview,
                files=(
                    *preview.files,
                    MigrationPlanFile(CMS_ACCESS_POLICY_PATH, plan.proposed_content),
                ),
                changed_paths=(*preview.changed_paths, CMS_ACCESS_POLICY_PATH),
            )

        async def existing_policy_pr_url(
            repository: GitHubRepository,
            access_token: SecretStr,
            plan: CmsAccessPolicyPlan | None,
            base_commit_sha: str | None,
            *,
            policy_only: bool,
        ) -> str | None:
            if (
                plan is None
                or base_commit_sha is None
                or plan.change is CmsAccessPolicyChange.UNCHANGED
                or (not plan.needs_separate_pull_request and not policy_only)
            ):
                return None
            candidates = await app.state.github_client.list_open_repository_pull_requests(
                access_token, repository, plan.default_branch
            )
            current = current_policy_pull_request(
                candidates, repository, plan.default_branch, base_commit_sha
            )
            return current.html_url if current is not None else None

        async def open_migration_pull_requests(
            repository: GitHubRepository, access_token: SecretStr, target_branch: str
        ) -> tuple[GitHubPullRequestDetails, ...]:
            """Treat GitHub PRs in the worker's reserved branch namespace as current state."""
            candidates = await app.state.github_client.list_open_repository_pull_requests(
                access_token, repository, target_branch
            )
            return tuple(
                pr
                for pr in candidates
                if pr.state == "open"
                and pr.head.repo is not None
                and pr.head.repo.id == repository.id
                and pr.base.repo is not None
                and pr.base.repo.id == repository.id
                and pr.base.ref == target_branch
                and re.fullmatch(r"comic-git/cms-enable/[0-9a-f]{12}(?:-[1-9][0-9]*)?", pr.head.ref)
            )

        async def migration_branch_tree(
            repository: GitHubRepository, access_token: SecretStr, branch_name: str
        ) -> tuple[GitHubGitReference, GitHubGitTree]:
            branch = await app.state.github_client.get_repository_ref(
                access_token, repository, f"heads/{branch_name}"
            )
            commit = await app.state.github_client.get_repository_commit(
                access_token, repository, branch.object.sha
            )
            tree = await app.state.github_client.get_repository_tree(
                access_token, repository, commit.tree.sha
            )
            if tree.truncated:
                raise CmsMigrationRetryError
            return branch, tree

        async def available_migration_branch_name(
            repository: GitHubRepository, access_token: SecretStr, base_name: str
        ) -> tuple[str, GitHubGitReference | None]:
            """Reuse an orphan branch, but never revive a branch attached to a closed PR."""
            for suffix in range(0, 20):
                name = base_name if suffix == 0 else f"{base_name}-{suffix}"
                existing = await app.state.github_client.find_repository_ref(
                    access_token, repository, f"heads/{name}"
                )
                if existing is None:
                    return name, None
                attached = await app.state.github_client.list_repository_pull_requests_for_head(
                    access_token, repository, name
                )
                if not attached:
                    return name, existing
            raise CmsMigrationRetryError

        async def cms_enablement_repository_callback(
            oauth_state: OAuthState,
            authorization: str | SetupSession,
        ) -> HTMLResponse:
            """Render branches after proving the setup user's current repository access."""
            assert oauth_state.repository_id is not None
            assert oauth_state.installation_id is not None
            access_token = (
                authorization
                if isinstance(authorization, SetupSession)
                else await app.state.github_client.exchange_authorization_code(
                    authorization, oauth_state.repository_id
                )
            )
            github_user = await app.state.github_client.get_authenticated_user(
                access_token.access_token
            )
            build_access_policy(worker_settings).require_permitted(
                github_user.login, AccessOperation.ENROLLMENT
            )
            repository = await app.state.github_client.verify_bound_repository_access(
                access_token.access_token, oauth_state.installation_id, oauth_state.repository_id
            )
            await app.state.github_client.require_repository_administrator(
                access_token.access_token, repository, github_user.login
            )
            branches = await app.state.github_client.list_repository_branches(
                access_token.access_token, repository
            )
            if (
                not branches
                or repository.default_branch is None
                or repository.default_branch not in {branch.name for branch in branches}
            ):
                raise WorkerError
            branch_selection = app.state.state_manager.issue_cms_enablement_repository(
                repository.id,
                oauth_state.installation_id,
                correlation_nonce=(
                    authorization.nonce if isinstance(authorization, SetupSession) else None
                ),
            )
            log_event(
                logger,
                "cms_setup_branch_selection_ready",
                repository_id=repository.id,
                repository_owner=repository.owner.login,
                repository_name=repository.name,
                default_branch=repository.default_branch,
                branch_count=len(branches),
            )
            response = HTMLResponse(
                render_cms_branch_selection_page(
                    branch_selection.token,
                    repository,
                    tuple(
                        sorted(
                            branches,
                            key=lambda branch: (
                                branch.name != repository.default_branch,
                                branch.name.casefold(),
                            ),
                        )
                    ),
                )
            )
            app.state.state_manager.attach_correlation_cookie(response, branch_selection)
            app.state.setup_sessions.attach(
                response,
                access_token.access_token,
                branch_selection.correlation_nonce,
                installation_id=oauth_state.installation_id,
                repository_id=repository.id,
            )
            return response

        async def cms_enablement_branch_callback(
            oauth_state: OAuthState,
            authorization: str | SetupSession,
        ) -> HTMLResponse:
            """Render a verified engine-produced plan for the creator-selected repository branch."""
            assert oauth_state.repository_id is not None
            assert oauth_state.installation_id is not None
            assert oauth_state.target_branch is not None
            access_token = (
                authorization
                if isinstance(authorization, SetupSession)
                else await app.state.github_client.exchange_authorization_code(
                    authorization, oauth_state.repository_id
                )
            )
            github_user = await app.state.github_client.get_authenticated_user(
                access_token.access_token
            )
            build_access_policy(worker_settings).require_permitted(
                github_user.login, AccessOperation.ENROLLMENT
            )
            repository = await app.state.github_client.verify_bound_repository_access(
                access_token.access_token, oauth_state.installation_id, oauth_state.repository_id
            )
            await app.state.github_client.require_repository_administrator(
                access_token.access_token, repository, github_user.login
            )
            if repository.default_branch is None:
                raise CmsAccessPolicyPlanError
            if oauth_state.approved_origins is None:
                existing = await app.state.github_client.get_default_branch_cms_access_policy(
                    access_token.access_token, repository
                )
                current_origins: tuple[str, ...] = ()
                if existing is not None:
                    current_policy = load_cms_access_policy(existing)
                    if current_policy.repository_id != repository.id:
                        raise CmsAccessPolicyPlanError
                    current_origins = current_policy.authorized_origins
                approval_state = app.state.state_manager.issue_cms_enablement_branch(
                    repository.id,
                    oauth_state.installation_id,
                    oauth_state.target_branch,
                    correlation_nonce=(
                        authorization.nonce if isinstance(authorization, SetupSession) else None
                    ),
                )
                response = HTMLResponse(
                    render_cms_access_origins_page(
                        approval_state.token,
                        repository,
                        oauth_state.target_branch,
                        current_origins,
                        str(worker_settings.public_base_url).rstrip("/"),
                    )
                )
                app.state.state_manager.attach_correlation_cookie(response, approval_state)
                if not isinstance(authorization, SetupSession):
                    app.state.setup_sessions.attach(
                        response,
                        access_token.access_token,
                        approval_state.correlation_nonce,
                        installation_id=oauth_state.installation_id,
                        repository_id=repository.id,
                    )
                return response
            target_state, preview = await build_logged_cms_migration_preview(
                repository, access_token.access_token, oauth_state.target_branch
            )
            policy_plan = None
            default_commit_sha = None
            assert oauth_state.approved_origins is not None
            policy_plan, default_commit_sha = await build_cms_access_review(
                repository,
                access_token.access_token,
                oauth_state.target_branch,
                oauth_state.approved_origins,
            )
            if policy_plan.default_branch == preview.target_branch:
                if default_commit_sha != preview.base_commit_sha:
                    raise CmsMigrationReviewChangedError
                if (
                    policy_plan.change is not CmsAccessPolicyChange.UNCHANGED
                    and not preview.already_enabled
                ):
                    preview = include_cms_access_policy(preview, policy_plan)
            if preview.already_enabled and (
                policy_plan is None or policy_plan.change is CmsAccessPolicyChange.UNCHANGED
            ):
                log_event(
                    logger,
                    "cms_setup_already_enabled",
                    repository_id=repository.id,
                    target_branch=preview.target_branch,
                    engine_commit_sha=preview.engine_commit_sha,
                )
                response = HTMLResponse(
                    render_management_result_page(
                        f"CMS is already enabled for {repository.owner.login}/{repository.name} "
                        f"on branch {preview.target_branch}. No migration pull request is needed.",
                        heading="comic_git CMS is already enabled",
                    )
                )
                app.state.setup_sessions.clear(response)
                return response
            if preview.already_enabled:
                assert policy_plan is not None
                assert default_commit_sha is not None
                policy_pr_url = await existing_policy_pr_url(
                    repository,
                    access_token.access_token,
                    policy_plan,
                    default_commit_sha,
                    policy_only=True,
                )
                confirmation = app.state.state_manager.issue_cms_enablement_confirmation(
                    repository.id,
                    oauth_state.installation_id,
                    preview.target_branch,
                    preview.base_commit_sha,
                    preview.engine_selector,
                    preview.engine_commit_sha,
                    _migration_plan_digest(preview),
                    correlation_nonce=(
                        authorization.nonce if isinstance(authorization, SetupSession) else None
                    ),
                    approved_origins=oauth_state.approved_origins,
                    default_branch_commit_sha=default_commit_sha,
                    reviewed_policy_digest=_policy_plan_digest(policy_plan),
                )
                response = HTMLResponse(
                    render_cms_migration_summary_page(
                        confirmation.token,
                        repository,
                        preview,
                        (),
                        access_policy_plan=policy_plan,
                        existing_policy_pull_request_url=policy_pr_url,
                    )
                )
                app.state.state_manager.attach_correlation_cookie(response, confirmation)
                return response
            branch_name = migration_branch_name(preview.target_branch, preview.base_commit_sha)
            pull_requests = await open_migration_pull_requests(
                repository, access_token.access_token, preview.target_branch
            )
            current = max(
                (pr for pr in pull_requests if _matches_migration_branch(pr.head.ref, branch_name)),
                key=lambda pr: pr.number,
                default=None,
            )
            superseded = tuple(pr for pr in pull_requests if pr != current)
            comparison_tree = target_state.tree
            if current is not None:
                _, comparison_tree = await migration_branch_tree(
                    repository, access_token.access_token, current.head.ref
                )
            changes = await compare_migration_files(
                app.state.github_client,
                access_token.access_token,
                repository,
                target_state.tree,
                comparison_tree,
                preview,
            )
            up_to_date = (
                current is not None
                and not changes
                and _migration_body_has_engine_sha(current.body, preview.engine_commit_sha)
                and not superseded
                and (policy_plan is None or policy_plan.change is CmsAccessPolicyChange.UNCHANGED)
            )
            confirmation = app.state.state_manager.issue_cms_enablement_confirmation(
                repository.id,
                oauth_state.installation_id,
                preview.target_branch,
                preview.base_commit_sha,
                preview.engine_selector,
                preview.engine_commit_sha,
                _migration_plan_digest(preview),
                correlation_nonce=(
                    authorization.nonce if isinstance(authorization, SetupSession) else None
                ),
                approved_origins=oauth_state.approved_origins,
                default_branch_commit_sha=default_commit_sha,
                reviewed_policy_digest=(
                    _policy_plan_digest(policy_plan) if policy_plan is not None else None
                ),
            )
            response = HTMLResponse(
                render_cms_migration_summary_page(
                    confirmation.token,
                    repository,
                    preview,
                    changes,
                    current.html_url if current else None,
                    up_to_date,
                    superseded,
                    access_policy_plan=policy_plan,
                    existing_policy_pull_request_url=await existing_policy_pr_url(
                        repository,
                        access_token.access_token,
                        policy_plan,
                        default_commit_sha,
                        policy_only=False,
                    ),
                )
            )
            app.state.state_manager.attach_correlation_cookie(response, confirmation)
            return response

        async def cms_enablement_confirmation_callback(
            oauth_state: OAuthState,
            authorization: str | SetupSession,
        ) -> HTMLResponse:
            """Revalidate a reviewed plan, then create its one atomic migration pull request."""
            assert oauth_state.repository_id is not None
            assert oauth_state.installation_id is not None
            assert oauth_state.target_branch is not None
            assert oauth_state.base_commit_sha is not None
            assert oauth_state.engine_selector is not None
            assert oauth_state.engine_commit_sha is not None
            access_token = (
                authorization
                if isinstance(authorization, SetupSession)
                else await app.state.github_client.exchange_authorization_code(
                    authorization, oauth_state.repository_id
                )
            )
            github_user = await app.state.github_client.get_authenticated_user(
                access_token.access_token
            )
            build_access_policy(worker_settings).require_permitted(
                github_user.login, AccessOperation.ENROLLMENT
            )
            repository = await app.state.github_client.verify_bound_repository_access(
                access_token.access_token, oauth_state.installation_id, oauth_state.repository_id
            )
            await app.state.github_client.require_repository_administrator(
                access_token.access_token, repository, github_user.login
            )
            target_state, preview = await build_logged_cms_migration_preview(
                repository, access_token.access_token, oauth_state.target_branch
            )
            policy_plan = None
            policy_result = None
            if (
                oauth_state.approved_origins is None
                or oauth_state.default_branch_commit_sha is None
                or oauth_state.reviewed_policy_digest is None
            ):
                raise CmsMigrationReviewChangedError
            policy_plan, default_commit_sha = await build_cms_access_review(
                repository,
                access_token.access_token,
                oauth_state.target_branch,
                oauth_state.approved_origins,
            )
            if (
                default_commit_sha != oauth_state.default_branch_commit_sha
                or _policy_plan_digest(policy_plan) != oauth_state.reviewed_policy_digest
            ):
                raise CmsMigrationReviewChangedError
            if policy_plan.default_branch == preview.target_branch:
                if default_commit_sha != preview.base_commit_sha:
                    raise CmsMigrationReviewChangedError
                if (
                    policy_plan.change is not CmsAccessPolicyChange.UNCHANGED
                    and not preview.already_enabled
                ):
                    preview = include_cms_access_policy(preview, policy_plan)
            if preview.already_enabled and (
                policy_plan is None or policy_plan.change is CmsAccessPolicyChange.UNCHANGED
            ):
                raise CmsMigrationReviewChangedError
            if (
                preview.target_branch != oauth_state.target_branch
                or preview.base_commit_sha != oauth_state.base_commit_sha
                or preview.engine_selector != oauth_state.engine_selector
                or preview.engine_commit_sha != oauth_state.engine_commit_sha
            ):
                raise CmsMigrationReviewChangedError
            branch_name = migration_branch_name(preview.target_branch, preview.base_commit_sha)
            if _migration_plan_digest(preview) != oauth_state.reviewed_plan_digest:
                raise CmsMigrationReviewChangedError
            if preview.already_enabled:
                assert policy_plan is not None
                policy_result = await deliver_cms_access_policy(
                    app.state.github_client,
                    access_token.access_token,
                    repository,
                    policy_plan,
                    oauth_state.default_branch_commit_sha,
                )
                return HTMLResponse(
                    render_cms_migration_result_page(
                        None,
                        access_policy_pull_request_url=policy_result.pull_request_url,
                        unremoved_policy_branches=policy_result.unremoved_branches,
                    )
                )
            if policy_plan is not None and policy_plan.needs_separate_pull_request:
                policy_result = await deliver_cms_access_policy(
                    app.state.github_client,
                    access_token.access_token,
                    repository,
                    policy_plan,
                    oauth_state.default_branch_commit_sha,
                )
            pull_requests = await open_migration_pull_requests(
                repository, access_token.access_token, preview.target_branch
            )
            current = max(
                (pr for pr in pull_requests if _matches_migration_branch(pr.head.ref, branch_name)),
                key=lambda pr: pr.number,
                default=None,
            )
            if current is not None:
                branch, current_tree = await migration_branch_tree(
                    repository, access_token.access_token, current.head.ref
                )
                file_changes = await compare_migration_files(
                    app.state.github_client,
                    access_token.access_token,
                    repository,
                    target_state.tree,
                    current_tree,
                    preview,
                )
                if file_changes:
                    blobs = await app.state.github_client.create_repository_blobs(
                        access_token.access_token,
                        repository,
                        tuple(GitHubTreeChange(file.path, file.content) for file in preview.files),
                    )
                    tree = await app.state.github_client.create_repository_tree(
                        access_token.access_token, repository, target_state.tree.sha, blobs
                    )
                    commit = await app.state.github_client.create_repository_commit(
                        access_token.access_token,
                        repository,
                        "Refresh comic_git CMS migration",
                        tree.sha,
                        branch.object.sha,
                    )
                    try:
                        await app.state.github_client.update_repository_branch(
                            access_token.access_token, repository, current.head.ref, commit.sha
                        )
                    except GitHubClientError:
                        raise CmsMigrationRetryError from None
                refreshed_body = _refreshed_migration_body(current.body, preview)
                if refreshed_body != current.body:
                    try:
                        await app.state.github_client.update_repository_pull_request_body(
                            access_token.access_token, repository, current.number, refreshed_body
                        )
                    except GitHubClientError:
                        raise CmsMigrationRetryError from None
                pull_request = current
            else:
                branch_name, orphan = await available_migration_branch_name(
                    repository, access_token.access_token, branch_name
                )
                blobs = await app.state.github_client.create_repository_blobs(
                    access_token.access_token,
                    repository,
                    tuple(GitHubTreeChange(file.path, file.content) for file in preview.files),
                )
                tree = await app.state.github_client.create_repository_tree(
                    access_token.access_token, repository, target_state.tree.sha, blobs
                )
                if orphan is None:
                    commit = await app.state.github_client.create_repository_commit(
                        access_token.access_token,
                        repository,
                        "Enable comic_git CMS",
                        tree.sha,
                        target_state.base_commit_sha,
                    )
                    try:
                        await app.state.github_client.create_repository_branch(
                            access_token.access_token, repository, branch_name, commit.sha
                        )
                    except GitHubClientError:
                        raise CmsMigrationRetryError from None
                else:
                    _, orphan_tree = await migration_branch_tree(
                        repository, access_token.access_token, branch_name
                    )
                    if orphan_tree.sha != tree.sha:
                        commit = await app.state.github_client.create_repository_commit(
                            access_token.access_token,
                            repository,
                            "Refresh comic_git CMS migration",
                            tree.sha,
                            orphan.object.sha,
                        )
                        try:
                            await app.state.github_client.update_repository_branch(
                                access_token.access_token, repository, branch_name, commit.sha
                            )
                        except GitHubClientError:
                            raise CmsMigrationRetryError from None
                try:
                    pull_request = await app.state.github_client.create_repository_pull_request(
                        access_token.access_token,
                        repository,
                        "Enable comic_git CMS",
                        _migration_pull_request_body(preview),
                        branch_name,
                        preview.target_branch,
                    )
                except GitHubClientError:
                    raise CmsMigrationRetryError from None
            unremoved_branches: set[str] = set()
            deleted_branches: set[str] = set()
            github_client = app.state.github_client
            for old_pr in pull_requests:
                if old_pr.number != pull_request.number:
                    try:
                        await github_client.close_repository_pull_request(
                            access_token.access_token, repository, old_pr.number
                        )
                    except GitHubClientError:
                        raise CmsMigrationRetryError from None
                    old_branch = old_pr.head.ref
                    if old_branch == branch_name or old_branch in deleted_branches:
                        continue
                    try:
                        attached = await github_client.list_repository_pull_requests_for_head(
                            access_token.access_token, repository, old_branch
                        )
                        if any(pr.state == "open" for pr in attached):
                            continue
                        await github_client.delete_repository_branch(
                            access_token.access_token, repository, old_branch
                        )
                        deleted_branches.add(old_branch)
                        unremoved_branches.discard(old_branch)
                    except GitHubClientError:
                        unremoved_branches.add(old_branch)
                        log_event(
                            logger,
                            "cms_setup_superseded_branch_cleanup_failed",
                            severity=logging.WARNING,
                            repository_id=repository.id,
                            branch=old_branch,
                        )
            log_event(
                logger,
                "cms_setup_pull_request_ready",
                repository_id=repository.id,
                repository_owner=repository.owner.login,
                repository_name=repository.name,
                target_branch=preview.target_branch,
                base_commit_sha=preview.base_commit_sha,
                pull_request_url=pull_request.html_url,
            )
            return HTMLResponse(
                render_cms_migration_result_page(
                    pull_request.html_url,
                    tuple(sorted(unremoved_branches)),
                    access_policy_pull_request_url=(
                        policy_result.pull_request_url if policy_result is not None else None
                    ),
                    unremoved_policy_branches=(
                        policy_result.unremoved_branches if policy_result is not None else ()
                    ),
                )
            )

        @app.options("/auth/{repository_id}/refresh")
        async def auth_refresh_options(repository_id: int, request: Request) -> Response:
            """Allow valid browser preflight; the POST performs live authorization."""
            if repository_id <= 0:
                raise WorkerError
            origin = _require_bound_origin(request)
            return Response(
                status_code=204,
                headers=_cors_headers(origin),
            )

        @app.post("/auth/{repository_id}/refresh")
        async def auth_refresh(
            repository_id: int, request: Request, provider: str | None = None
        ) -> JSONResponse:
            """Refresh only after policy and repository verification for the request origin."""
            if provider != "github" or repository_id <= 0:
                raise WorkerError
            origin = _require_bound_origin(request)

            try:
                refresh_request = RefreshTokenRequest.model_validate(
                    {
                        name: values[0]
                        for name, values in parse_qs((await request.body()).decode("utf-8")).items()
                        if values
                    }
                )
                access_token = await app.state.github_client.refresh_user_access_token(
                    refresh_request.refresh_token
                )
                github_user = await app.state.github_client.get_authenticated_user(
                    access_token.access_token
                )
                build_access_policy(worker_settings).require_permitted(
                    github_user.login,
                    AccessOperation.REFRESH,
                )
                repository = await app.state.github_client.verify_repository_access(
                    access_token.access_token,
                    repository_id,
                )
                await authorize_cms_origin(access_token.access_token, repository, origin)
                payload = DecapTokenPayload(
                    access_token=access_token.access_token,
                    token_type=access_token.token_type,
                    refresh_token=access_token.refresh_token,
                    expires_in=access_token.expires_in,
                )
                return JSONResponse(payload.callback_data(), headers=_cors_headers(origin))
            except UnicodeDecodeError, ValidationError:
                raise WorkerError from None

    return app


app = create_app()


def _require_bound_origin(request: Request) -> str:
    """Validate the actual browser Origin header used for CMS token refresh."""
    origin = request.headers.get("origin")
    if origin is None:
        raise WorkerError
    try:
        return canonicalize_origin(origin)
    except ValueError:
        raise WorkerError from None


def _cors_headers(origin: str) -> dict[str, str]:
    """Return the narrow CORS response headers required by Decap's refresh fetch request."""
    return {
        "Access-Control-Allow-Origin": origin,
        "Access-Control-Allow-Methods": "POST, OPTIONS",
        "Access-Control-Allow-Headers": "Content-Type",
        "Vary": "Origin",
    }


def _migration_plan_digest(preview: CmsMigrationPreview) -> str:
    """Identify generated content, not just the engine ref that produced it."""
    serialized = json.dumps(
        [(file.path, file.content) for file in preview.files],
        ensure_ascii=False,
        separators=(",", ":"),
    )
    return hashlib.sha256(serialized.encode("utf-8")).hexdigest()


def _policy_plan_digest(plan: CmsAccessPolicyPlan) -> str:
    """Bind a signed confirmation to the default branch and exact proposed policy."""
    content = f"{plan.default_branch}\0{plan.proposed_content}".encode()
    return hashlib.sha256(content).hexdigest()


def _matches_migration_branch(branch_name: str, base_name: str) -> bool:
    """Match one target revision, including replacement attempts after a closed PR."""
    return branch_name == base_name or bool(
        re.fullmatch(re.escape(base_name) + r"-[1-9][0-9]*", branch_name)
    )


def _migration_body_has_engine_sha(body: str | None, engine_sha: str) -> bool:
    return body is not None and f"Resolved engine SHA: `{engine_sha}`" in body.splitlines()


def _refreshed_migration_body(body: str | None, preview: CmsMigrationPreview) -> str:
    """Refresh only the worker-owned audit line, preserving creator edits around it."""
    if body is None:
        return _migration_pull_request_body(preview)
    line = f"Resolved engine SHA: `{preview.engine_commit_sha}`"
    updated, count = re.subn(r"(?m)^Resolved engine SHA: `[0-9a-f]{40}`$", line, body, count=1)
    if count:
        return updated
    return body.rstrip() + "\n\n" + line


def _migration_pull_request_body(preview: CmsMigrationPreview) -> str:
    return _migration_pull_request_body_for(
        preview.engine_selector, preview.engine_commit_sha, preview.target_branch
    )


def _migration_pull_request_body_for(
    engine_selector: str, engine_commit_sha: str, target_branch: str
) -> str:
    return (
        f"Engine selector: `{engine_selector}`\n\n"
        f"Resolved engine SHA: `{engine_commit_sha}`\n\n"
        f"Target branch: `{target_branch}`"
    )


def _cms_enablement_input(
    settings: WorkerSettings,
    repository: object,
    target_branch: str,
) -> dict[str, object]:
    """Provide the fixed worker-owned CMS settings the engine serializer may include in TOML."""
    if settings.public_base_url is None:
        raise WorkerError
    try:
        owner = repository.owner.login
        name = repository.name
    except AttributeError:
        raise WorkerError from None
    if not target_branch:
        raise WorkerError
    backend_base_url = str(settings.public_base_url).rstrip("/")
    return {
        "repository": f"{owner}/{name}",
        "branch": target_branch,
        "backend_base_url": backend_base_url,
        "backend_auth_endpoint": f"auth/{repository.id}",
        "editorial_workflow": False,
    }
