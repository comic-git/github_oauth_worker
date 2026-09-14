"""FastAPI application composition for the GitHub OAuth worker."""

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
from github_oauth_worker.config import WorkerSettings, load_settings
from github_oauth_worker.decap_protocol import (
    DecapTokenPayload,
    render_error_callback_page,
    render_handshake_page,
    render_setup_handshake_page,
    render_success_callback_page,
)
from github_oauth_worker.enrollment import render_enrollment_confirmation_page
from github_oauth_worker.errors import WorkerError
from github_oauth_worker.github_client import GitHubAppClient
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


def create_app(
    settings: WorkerSettings | None = None,
    binding_store: BindingStore | None = None,
    github_client: GitHubAppClient | None = None,
) -> FastAPI:
    """Create the worker and compose ready-mode dependencies only after settings validation."""
    worker_settings = settings or load_settings()
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
            """Receive GitHub's setup redirect and begin a non-secret CMS opener handshake."""
            if installation_id <= 0:
                raise WorkerError
            return HTMLResponse(render_setup_handshake_page(installation_id))

        @app.post("/setup/handshake")
        def setup_handshake(handshake: SetupHandshakeRequest) -> RedirectResponse:
            """Require fresh GitHub user authorization before trusting a setup installation ID."""
            issued_state = app.state.state_manager.issue_setup_installation(
                handshake.origin,
                handshake.installation_id,
            )
            return redirect_to_github(issued_state)

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
            assert oauth_state.origin is not None

            try:
                if not code:
                    raise WorkerError
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
                if oauth_state.flow is not OAuthFlow.DECAP:
                    raise WorkerError
                assert oauth_state.repository_id is not None
                assert oauth_state.installation_id is not None
                binding = await app.state.binding_store.get_binding(oauth_state.repository_id)
                if (
                    binding is None
                    or binding.status is not BindingStatus.ACTIVE
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
                repository = await app.state.github_client.verify_bound_repository_access(
                    access_token.access_token,
                    binding.installation_id,
                    binding.repository_id,
                )
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
            await app.state.binding_store.create_binding(
                RepositoryBinding.create(
                    repository_id=repository.id,
                    repository_owner=repository.owner.login,
                    repository_name=repository.name,
                    installation_id=oauth_state.installation_id,
                    origin=origin,
                )
            )
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
            except (UnicodeDecodeError, ValidationError, ValueError):
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
                repository = await app.state.github_client.verify_bound_repository_access(
                    access_token.access_token,
                    binding.installation_id,
                    binding.repository_id,
                )
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
            except (UnicodeDecodeError, ValidationError):
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
