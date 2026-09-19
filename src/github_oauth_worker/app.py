"""FastAPI application composition for the GitHub OAuth worker."""

from datetime import UTC, datetime, timedelta
from urllib.parse import parse_qs, urlencode

from fastapi import FastAPI, Request, Response
from fastapi.responses import HTMLResponse, JSONResponse, RedirectResponse
from pydantic import BaseModel, ConfigDict, SecretStr, ValidationError

from github_oauth_worker.bindings import (
    BindingStatus,
    BindingStore,
    FirestoreBindingStore,
    RepositoryBinding,
    canonicalize_origin,
)
from github_oauth_worker.cms_enablement import (
    CmsMigrationPreview,
    TargetRepositoryState,
    build_migration_preview,
    read_target_repository_state,
    resolve_official_engine_commit,
)
from github_oauth_worker.cms_setup import (
    render_cms_migration_result_page,
    render_cms_migration_summary_page,
    render_cms_repository_selection_page,
    render_cms_setup_start_page,
)
from github_oauth_worker.config import WorkerSettings, load_settings
from github_oauth_worker.decap_protocol import (
    DecapTokenPayload,
    render_error_callback_page,
    render_handshake_page,
    render_management_result_page,
    render_origin_completion_handshake_page,
    render_origin_migration_handshake_page,
    render_success_callback_page,
)
from github_oauth_worker.enablement_operations import (
    EnablementOperation,
    EnablementOperationStore,
    FirestoreEnablementOperationStore,
)
from github_oauth_worker.engine_resolution import EngineSelectorError, EngineSelectorPolicy
from github_oauth_worker.enrollment import render_enrollment_confirmation_page
from github_oauth_worker.errors import WorkerError
from github_oauth_worker.github_client import (
    GitHubAccessVerificationError,
    GitHubAppClient,
    GitHubRepository,
    GitHubTreeChange,
)
from github_oauth_worker.logging import configure_event_logger, log_event
from github_oauth_worker.policy import AccessOperation, build_access_policy
from github_oauth_worker.state import IssuedOAuthState, OAuthFlow, OAuthState, OAuthStateManager


class AuthHandshakeRequest(BaseModel):
    """The origin received by the popup page from Decap's opener-message event."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    origin: str


class RefreshTokenRequest(BaseModel):
    """The only Decap refresh body field needed by the worker."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    refresh_token: SecretStr


class EnrollmentSelectionRequest(BaseModel):
    """The signed enrollment state and selected verified repository from the confirmation form."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: str
    selection: str


class SetupHandshakeRequest(BaseModel):
    """An untrusted App setup installation ID paired with the browser-captured CMS origin."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    origin: str
    installation_id: int


class CmsSetupSelectionRequest(BaseModel):
    """A signed direct-setup state and repository chosen from its verified installation list."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: str
    repository_id: int


class CmsSetupConfirmationRequest(BaseModel):
    """A signed reviewed migration summary whose final authorization must be refreshed."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    state: str


class OriginMigrationHandshakeRequest(BaseModel):
    """A current browser origin and desired destination captured by the migration popup."""

    model_config = ConfigDict(extra="forbid", frozen=True)

    origin: str
    target_origin: str


def create_app(
    settings: WorkerSettings | None = None,
    binding_store: BindingStore | None = None,
    operation_store: EnablementOperationStore | None = None,
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

        def redirect_to_github(
            issued_state: IssuedOAuthState,
            repository_id: int | None = None,
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
            response = RedirectResponse(
                "https://github.com/login/oauth/authorize?" + urlencode(parameters),
                status_code=302,
            )
            app.state.state_manager.attach_correlation_cookie(response, issued_state)
            return response

        app.state.binding_store = binding_store or FirestoreBindingStore.from_settings(
            worker_settings
        )
        app.state.operation_store = operation_store or (
            FirestoreEnablementOperationStore.from_settings(worker_settings)
        )
        app.state.github_client = github_client or GitHubAppClient.from_settings(worker_settings)
        app.state.state_manager = OAuthStateManager.from_settings(worker_settings)

    @app.exception_handler(WorkerError)
    async def handle_worker_error(_: Request, error: WorkerError) -> JSONResponse:
        """Return only the deliberate public error text for expected worker failures."""
        return JSONResponse(
            status_code=error.status_code,
            content={"error": error.public_message},
        )

    @app.get("/health")
    def health() -> dict[str, str]:
        """Report readiness without exposing configuration or secret state."""
        return {"status": worker_settings.service_mode.value}

    if worker_settings.service_mode.value == "ready":

        @app.get("/auth")
        def auth() -> HTMLResponse:
            """Open a non-secret Decap handshake page before the browser knows its exact origin."""
            return HTMLResponse(render_handshake_page())

        @app.get("/setup")
        def setup(installation_id: int) -> HTMLResponse:
            """Receive GitHub's direct App setup redirect without trusting its installation ID."""
            if installation_id <= 0:
                raise WorkerError
            return HTMLResponse(render_cms_setup_start_page(installation_id))

        @app.post("/setup/continue")
        async def setup_continue(request: Request) -> RedirectResponse:
            """Start fresh authorization before acting on GitHub's untrusted installation query."""
            try:
                form_data = parse_qs((await request.body()).decode("utf-8"))
                installation_id = int(form_data["installation_id"][0])
                if installation_id <= 0:
                    raise ValueError
            except KeyError, UnicodeDecodeError, ValueError:
                raise WorkerError from None
            return redirect_to_github(
                app.state.state_manager.issue_cms_enablement_installation(installation_id)
            )

        @app.post("/setup/select")
        async def setup_select(request: Request) -> RedirectResponse:
            """Request fresh repository-restricted authorization after creator selection."""
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
            issued_state = app.state.state_manager.issue_cms_enablement_repository(
                selection.repository_id, oauth_state.installation_id
            )
            return redirect_to_github(issued_state, selection.repository_id)

        @app.post("/setup/confirm")
        async def setup_confirm(request: Request) -> RedirectResponse:
            """Require fresh repository-scoped authorization for the exact reviewed plan."""
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
            assert oauth_state.base_commit_sha is not None
            assert oauth_state.engine_selector is not None
            assert oauth_state.engine_commit_sha is not None
            issued_state = app.state.state_manager.issue_cms_enablement_confirmation(
                oauth_state.repository_id,
                oauth_state.installation_id,
                oauth_state.base_commit_sha,
                oauth_state.engine_selector,
                oauth_state.engine_commit_sha,
            )
            return redirect_to_github(issued_state, oauth_state.repository_id)

        @app.post("/setup/handshake")
        def setup_handshake(handshake: SetupHandshakeRequest) -> RedirectResponse:
            """Require fresh GitHub user authorization before trusting a setup installation ID."""
            issued_state = app.state.state_manager.issue_setup_installation(
                handshake.origin,
                handshake.installation_id,
            )
            return redirect_to_github(issued_state)

        @app.get("/origins/migrate")
        def origin_migration(target_origin: str) -> HTMLResponse:
            """Open an existing CMS site's migration popup without trusting its query origin."""
            try:
                return HTMLResponse(render_origin_migration_handshake_page(target_origin))
            except ValueError:
                raise WorkerError from None

        @app.post("/origins/migrate/handshake")
        async def origin_migration_handshake(
            handshake: OriginMigrationHandshakeRequest,
        ) -> RedirectResponse:
            """Authorize a migration only after the source origin resolves to an active binding."""
            binding = await app.state.binding_store.get_binding_for_origin(handshake.origin)
            if binding is None or binding.status is not BindingStatus.ACTIVE:
                raise WorkerError
            try:
                source_origin = canonicalize_origin(handshake.origin)
                target_origin = canonicalize_origin(handshake.target_origin)
            except ValueError:
                raise WorkerError from None
            issued_state = app.state.state_manager.issue_origin_migration(
                source_origin,
                target_origin,
                binding.repository_id,
                binding.installation_id,
            )
            return redirect_to_github(issued_state, binding.repository_id)

        @app.get("/origins/complete")
        def complete_origin_migration() -> HTMLResponse:
            """Open a destination CMS popup that captures its actual origin before completion."""
            return HTMLResponse(render_origin_completion_handshake_page())

        @app.post("/origins/complete/handshake")
        async def complete_origin_migration_handshake(
            handshake: AuthHandshakeRequest,
        ) -> HTMLResponse:
            """Activate only a non-expired pending origin whose exact opener origin matches it."""
            await app.state.binding_store.complete_origin_migration(
                handshake.origin,
                timedelta(seconds=worker_settings.origin_grace_period_seconds),
            )
            return HTMLResponse(render_management_result_page("CMS origin migration is complete."))

        @app.post("/auth/handshake")
        async def auth_handshake(handshake: AuthHandshakeRequest) -> RedirectResponse:
            """Resolve a bound opener origin, then redirect its binding to GitHub authorization."""
            binding = await app.state.binding_store.get_binding_for_origin(handshake.origin)
            if binding is None:
                issued_state = app.state.state_manager.issue_enrollment(handshake.origin)
                return redirect_to_github(issued_state)
            if binding.status is not BindingStatus.ACTIVE:
                raise WorkerError

            issued_state = app.state.state_manager.issue_decap(
                handshake.origin,
                binding.repository_id,
                binding.installation_id,
            )
            return redirect_to_github(issued_state, binding.repository_id)

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
                    raise WorkerError
                if oauth_state.flow is OAuthFlow.CMS_ENABLEMENT_INSTALLATION:
                    return await cms_enablement_installation_callback(oauth_state, code, state)
                if oauth_state.flow is OAuthFlow.CMS_ENABLEMENT_REPOSITORY:
                    return await cms_enablement_repository_callback(oauth_state, code)
                if oauth_state.flow is OAuthFlow.CMS_ENABLEMENT_CONFIRMATION:
                    return await cms_enablement_confirmation_callback(oauth_state, code)
                assert oauth_state.origin is not None
                if oauth_state.flow is OAuthFlow.ENROLLMENT:
                    return await enrollment_callback(oauth_state.origin, code, state)
                if oauth_state.flow is OAuthFlow.SETUP:
                    if oauth_state.repository_id is None:
                        return await setup_installation_callback(
                            oauth_state.origin,
                            oauth_state,
                            code,
                            state,
                        )
                    return await setup_callback(oauth_state.origin, oauth_state, code)
                if oauth_state.flow is OAuthFlow.ORIGIN_MIGRATION:
                    return await origin_migration_callback(oauth_state, code)
                if oauth_state.flow is not OAuthFlow.DECAP:
                    raise WorkerError
                assert oauth_state.repository_id is not None
                assert oauth_state.installation_id is not None
                binding = await app.state.binding_store.get_binding_for_origin(oauth_state.origin)
                if (
                    binding is None
                    or binding.status is not BindingStatus.ACTIVE
                    or binding.repository_id != oauth_state.repository_id
                    or binding.installation_id != oauth_state.installation_id
                    or oauth_state.origin not in {record.origin for record in binding.origins}
                ):
                    raise WorkerError

                access_token = await app.state.github_client.exchange_authorization_code(
                    code,
                    binding.repository_id,
                )
                github_user = await app.state.github_client.get_authenticated_user(
                    access_token.access_token
                )
                build_access_policy(worker_settings).require_permitted(
                    github_user.login,
                    AccessOperation.AUTHORIZATION,
                )
                try:
                    repository = await app.state.github_client.verify_bound_repository_access(
                        access_token.access_token,
                        binding.installation_id,
                        binding.repository_id,
                    )
                except GitHubAccessVerificationError:
                    await app.state.binding_store.mark_recovery_required(binding.repository_id)
                    raise
                await app.state.binding_store.refresh_repository_metadata(
                    binding.repository_id,
                    repository.owner.login,
                    repository.name,
                    binding.installation_id,
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
                )
                if oauth_state.flow in {
                    OAuthFlow.CMS_ENABLEMENT_INSTALLATION,
                    OAuthFlow.CMS_ENABLEMENT_REPOSITORY,
                    OAuthFlow.CMS_ENABLEMENT_CONFIRMATION,
                }:
                    return HTMLResponse(
                        render_management_result_page(
                            error.public_message,
                            heading="comic_git CMS setup could not continue",
                            diagnostic_code=error.diagnostic_code,
                        ),
                        status_code=error.status_code,
                    )
                if oauth_state.flow is OAuthFlow.ORIGIN_MIGRATION:
                    return HTMLResponse(
                        render_management_result_page(
                            error.public_message,
                            heading="CMS origin migration could not continue",
                            diagnostic_code=error.diagnostic_code,
                        ),
                        status_code=error.status_code,
                    )
                return HTMLResponse(
                    render_error_callback_page(oauth_state.origin, error.public_message),
                    status_code=error.status_code,
                )

        async def enrollment_callback(
            origin: str,
            code: str,
            state_token: str | None,
        ) -> HTMLResponse:
            """Show verified App repository choices after initial policy-gated authorization."""
            access_token = await app.state.github_client.exchange_authorization_code(code)
            github_user = await app.state.github_client.get_authenticated_user(
                access_token.access_token
            )
            build_access_policy(worker_settings).require_permitted(
                github_user.login,
                AccessOperation.ENROLLMENT,
            )
            installations = await app.state.github_client.list_accessible_installations(
                access_token.access_token
            )
            choices = []
            for installation in installations:
                repositories = await app.state.github_client.list_repositories_for_installation(
                    access_token.access_token,
                    installation.id,
                )
                choices.extend((installation.id, repository) for repository in repositories)
            if not choices or state_token is None:
                raise WorkerError
            return HTMLResponse(
                render_enrollment_confirmation_page(origin, state_token, tuple(choices))
            )

        async def setup_callback(
            origin: str,
            oauth_state: OAuthState,
            code: str,
        ) -> HTMLResponse:
            """Use fresh authorization to verify the selected App repository before binding it."""
            assert oauth_state.repository_id is not None
            assert oauth_state.installation_id is not None
            access_token = await app.state.github_client.exchange_authorization_code(
                code,
                oauth_state.repository_id,
            )
            github_user = await app.state.github_client.get_authenticated_user(
                access_token.access_token
            )
            build_access_policy(worker_settings).require_permitted(
                github_user.login,
                AccessOperation.ENROLLMENT,
            )
            repository = await app.state.github_client.verify_bound_repository_access(
                access_token.access_token,
                oauth_state.installation_id,
                oauth_state.repository_id,
            )
            recovered_binding = RepositoryBinding.create(
                repository_id=repository.id,
                repository_owner=repository.owner.login,
                repository_name=repository.name,
                installation_id=oauth_state.installation_id,
                origin=origin,
            )
            existing_binding = await app.state.binding_store.get_binding(repository.id)
            if existing_binding is None:
                await app.state.binding_store.create_binding(recovered_binding)
            else:
                await app.state.binding_store.recover_binding(recovered_binding)
            return HTMLResponse(
                render_success_callback_page(
                    origin,
                    DecapTokenPayload(
                        access_token=access_token.access_token,
                        token_type=access_token.token_type,
                        refresh_token=access_token.refresh_token,
                        expires_in=access_token.expires_in,
                    ),
                )
            )

        async def cms_enablement_installation_callback(
            oauth_state: OAuthState,
            code: str,
            state_token: str | None,
        ) -> HTMLResponse:
            """List only repositories proven visible to the freshly authorized App installation."""
            assert oauth_state.installation_id is not None
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
            installation_ids = {installation.id for installation in installations}
            if oauth_state.installation_id not in installation_ids:
                raise WorkerError
            repositories = await app.state.github_client.list_repositories_for_installation(
                access_token.access_token, oauth_state.installation_id
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
            return HTMLResponse(
                render_cms_repository_selection_page(
                    state_token, oauth_state.installation_id, repositories
                )
            )

        async def build_logged_cms_migration_preview(
            repository: GitHubRepository,
            user_access_token: SecretStr,
        ) -> tuple[TargetRepositoryState, CmsMigrationPreview]:
            """Log non-secret setup milestones around the bounded migration-planning boundary."""
            repository_fields = {
                "repository_id": repository.id,
                "repository_owner": repository.owner.login,
                "repository_name": repository.name,
                "default_branch": repository.default_branch,
            }
            log_event(logger, "cms_setup_repository_verified", **repository_fields)
            target_state = await read_target_repository_state(
                app.state.github_client, user_access_token, repository
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
                _cms_enablement_input(worker_settings, repository),
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

        async def cms_enablement_repository_callback(
            oauth_state: OAuthState,
            code: str,
        ) -> HTMLResponse:
            """Render a verified engine-produced plan before a direct setup can request a PR."""
            assert oauth_state.repository_id is not None
            assert oauth_state.installation_id is not None
            access_token = await app.state.github_client.exchange_authorization_code(
                code, oauth_state.repository_id
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
            _, preview = await build_logged_cms_migration_preview(
                repository, access_token.access_token
            )
            confirmation = app.state.state_manager.issue_cms_enablement_confirmation(
                repository.id,
                oauth_state.installation_id,
                preview.base_commit_sha,
                preview.engine_selector,
                preview.engine_commit_sha,
            )
            return HTMLResponse(
                render_cms_migration_summary_page(confirmation.token, repository, preview)
            )

        async def cms_enablement_confirmation_callback(
            oauth_state: OAuthState,
            code: str,
        ) -> HTMLResponse:
            """Revalidate a reviewed plan, then create its one atomic migration pull request."""
            assert oauth_state.repository_id is not None
            assert oauth_state.installation_id is not None
            assert oauth_state.base_commit_sha is not None
            assert oauth_state.engine_selector is not None
            assert oauth_state.engine_commit_sha is not None
            access_token = await app.state.github_client.exchange_authorization_code(
                code, oauth_state.repository_id
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
                repository, access_token.access_token
            )
            if (
                preview.base_commit_sha != oauth_state.base_commit_sha
                or preview.engine_selector != oauth_state.engine_selector
                or preview.engine_commit_sha != oauth_state.engine_commit_sha
            ):
                raise WorkerError
            branch_name = f"comic-git/cms-enable/{preview.base_commit_sha[:12]}"
            operation = await app.state.operation_store.put_planned(
                EnablementOperation.create(
                    repository_id=repository.id,
                    base_commit_sha=preview.base_commit_sha,
                    engine_selector=preview.engine_selector,
                    engine_commit_sha=preview.engine_commit_sha,
                    branch_name=branch_name,
                )
            )
            if operation.pull_request_url is not None:
                log_event(
                    logger,
                    "cms_setup_existing_pull_request_returned",
                    repository_id=repository.id,
                    repository_owner=repository.owner.login,
                    repository_name=repository.name,
                    base_commit_sha=preview.base_commit_sha,
                    pull_request_url=operation.pull_request_url,
                )
                return HTMLResponse(render_cms_migration_result_page(operation.pull_request_url))
            claim = await app.state.operation_store.claim_writing(
                repository.id, preview.base_commit_sha
            )
            if not claim.acquired:
                existing_pr = await app.state.github_client.find_open_repository_pull_request(
                    access_token.access_token, repository, claim.operation.branch_name
                )
                if existing_pr is None:
                    raise WorkerError
                operation = await app.state.operation_store.complete(
                    repository.id,
                    preview.base_commit_sha,
                    existing_pr.number,
                    existing_pr.html_url,
                )
                log_event(
                    logger,
                    "cms_setup_existing_pull_request_recovered",
                    repository_id=repository.id,
                    repository_owner=repository.owner.login,
                    repository_name=repository.name,
                    base_commit_sha=preview.base_commit_sha,
                    pull_request_url=operation.pull_request_url,
                )
                return HTMLResponse(
                    render_cms_migration_result_page(operation.pull_request_url or "")
                )
            log_event(
                logger,
                "cms_setup_migration_write_started",
                repository_id=repository.id,
                repository_owner=repository.owner.login,
                repository_name=repository.name,
                base_commit_sha=preview.base_commit_sha,
                branch_name=branch_name,
                planned_file_count=len(preview.files),
            )
            changes = tuple(GitHubTreeChange(file.path, file.content) for file in preview.files)
            blobs = await app.state.github_client.create_repository_blobs(
                access_token.access_token, repository, changes
            )
            tree = await app.state.github_client.create_repository_tree(
                access_token.access_token, repository, target_state.tree.sha, blobs
            )
            commit = await app.state.github_client.create_repository_commit(
                access_token.access_token,
                repository,
                "Enable comic_git CMS",
                tree.sha,
                target_state.base_commit_sha,
            )
            log_event(
                logger,
                "cms_setup_migration_commit_created",
                repository_id=repository.id,
                repository_owner=repository.owner.login,
                repository_name=repository.name,
                base_commit_sha=preview.base_commit_sha,
                commit_sha=commit.sha,
                branch_name=branch_name,
            )
            await app.state.github_client.create_repository_branch(
                access_token.access_token, repository, branch_name, commit.sha
            )
            pull_request = await app.state.github_client.create_repository_pull_request(
                access_token.access_token,
                repository,
                "Enable comic_git CMS",
                (
                    f"Engine selector: `{preview.engine_selector}`\n\n"
                    f"Resolved engine SHA: `{preview.engine_commit_sha}`"
                ),
                branch_name,
                repository.default_branch or "",
            )
            operation = await app.state.operation_store.complete(
                repository.id, preview.base_commit_sha, pull_request.number, pull_request.html_url
            )
            log_event(
                logger,
                "cms_setup_pull_request_created",
                repository_id=repository.id,
                repository_owner=repository.owner.login,
                repository_name=repository.name,
                base_commit_sha=preview.base_commit_sha,
                pull_request_number=pull_request.number,
                pull_request_url=pull_request.html_url,
            )
            return HTMLResponse(render_cms_migration_result_page(operation.pull_request_url or ""))

        async def origin_migration_callback(
            oauth_state: OAuthState,
            code: str,
        ) -> HTMLResponse:
            """Create a pending destination only after fresh owner policy and App access checks."""
            assert oauth_state.origin is not None
            assert oauth_state.target_origin is not None
            assert oauth_state.repository_id is not None
            assert oauth_state.installation_id is not None
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
            try:
                repository = await app.state.github_client.verify_bound_repository_access(
                    access_token.access_token,
                    oauth_state.installation_id,
                    oauth_state.repository_id,
                )
            except GitHubAccessVerificationError:
                await app.state.binding_store.mark_recovery_required(oauth_state.repository_id)
                raise
            await app.state.binding_store.refresh_repository_metadata(
                oauth_state.repository_id,
                repository.owner.login,
                repository.name,
                oauth_state.installation_id,
            )
            await app.state.binding_store.begin_origin_migration(
                oauth_state.repository_id,
                oauth_state.origin,
                oauth_state.target_origin,
                datetime.now(UTC) + timedelta(seconds=worker_settings.oauth_state_ttl_seconds),
            )
            return HTMLResponse(
                render_management_result_page(
                    "CMS origin migration is ready to complete at the destination site."
                )
            )

        async def setup_installation_callback(
            origin: str,
            oauth_state: OAuthState,
            code: str,
            state_token: str | None,
        ) -> HTMLResponse:
            """Prove the setup installation is user-visible before offering its repositories."""
            assert oauth_state.installation_id is not None
            access_token = await app.state.github_client.exchange_authorization_code(code)
            github_user = await app.state.github_client.get_authenticated_user(
                access_token.access_token
            )
            build_access_policy(worker_settings).require_permitted(
                github_user.login,
                AccessOperation.ENROLLMENT,
            )
            installations = await app.state.github_client.list_accessible_installations(
                access_token.access_token
            )
            if oauth_state.installation_id not in {
                installation.id for installation in installations
            }:
                raise WorkerError
            repositories = await app.state.github_client.list_repositories_for_installation(
                access_token.access_token,
                oauth_state.installation_id,
            )
            choices = tuple(
                (oauth_state.installation_id, repository) for repository in repositories
            )
            if not choices or state_token is None:
                raise WorkerError
            return HTMLResponse(render_enrollment_confirmation_page(origin, state_token, choices))

        @app.post("/enroll/select")
        async def enroll_select(request: Request) -> RedirectResponse:
            """Require fresh authorization after explicit repository selection confirmation."""
            form_data = parse_qs((await request.body()).decode("utf-8"))
            try:
                selection = EnrollmentSelectionRequest.model_validate(
                    {name: values[0] for name, values in form_data.items() if values}
                )
                oauth_state = app.state.state_manager.consume_from_cookies(
                    selection.state,
                    request.cookies,
                )
                if oauth_state.flow not in (OAuthFlow.ENROLLMENT, OAuthFlow.SETUP):
                    raise ValueError("Selection state must come from enrollment or setup.")
                installation_id_text, repository_id_text = selection.selection.split(
                    ":",
                    maxsplit=1,
                )
                installation_id = int(installation_id_text)
                if (
                    oauth_state.flow is OAuthFlow.SETUP
                    and oauth_state.installation_id != installation_id
                ):
                    raise ValueError("The selected installation does not match setup state.")
                issued_state = app.state.state_manager.issue_setup(
                    oauth_state.origin or "",
                    int(repository_id_text),
                    installation_id,
                )
            except UnicodeDecodeError, ValidationError, ValueError:
                raise WorkerError from None
            return redirect_to_github(issued_state, int(repository_id_text))

        @app.options("/auth/refresh")
        async def auth_refresh_options(request: Request) -> Response:
            """Grant browser CORS preflight only to a currently bound exact CMS origin."""
            origin = _require_bound_origin(request)
            binding = await app.state.binding_store.get_binding_for_origin(origin)
            if binding is None or binding.status is not BindingStatus.ACTIVE:
                raise WorkerError
            return Response(
                status_code=204,
                headers=_cors_headers(origin),
            )

        @app.post("/auth/refresh")
        async def auth_refresh(request: Request, provider: str | None = None) -> JSONResponse:
            """Refresh only after policy and repository verification for the request origin."""
            if provider != "github":
                raise WorkerError
            origin = _require_bound_origin(request)
            binding = await app.state.binding_store.get_binding_for_origin(origin)
            if binding is None or binding.status is not BindingStatus.ACTIVE:
                raise WorkerError

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
                try:
                    repository = await app.state.github_client.verify_bound_repository_access(
                        access_token.access_token,
                        binding.installation_id,
                        binding.repository_id,
                    )
                except GitHubAccessVerificationError:
                    await app.state.binding_store.mark_recovery_required(binding.repository_id)
                    raise
                await app.state.binding_store.refresh_repository_metadata(
                    binding.repository_id,
                    repository.owner.login,
                    repository.name,
                    binding.installation_id,
                )
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
    """Validate the browser Origin header before it is used as a binding lookup key."""
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


def _cms_enablement_input(
    settings: WorkerSettings,
    repository: object,
) -> dict[str, object]:
    """Provide the fixed worker-owned CMS settings the engine serializer may include in TOML."""
    if settings.public_base_url is None:
        raise WorkerError
    try:
        owner = repository.owner.login
        name = repository.name
        branch = repository.default_branch
    except AttributeError:
        raise WorkerError from None
    if branch is None:
        raise WorkerError
    backend_base_url = str(settings.public_base_url).rstrip("/")
    return {
        "repository": f"{owner}/{name}",
        "branch": branch,
        "backend_base_url": backend_base_url,
        "backend_auth_endpoint": f"{backend_base_url}/auth",
        "editorial_workflow": False,
    }
