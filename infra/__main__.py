"""Build and deploy one environment's Cloud Run revision and optional runtime secrets."""

import pulumi
import pulumi_docker as docker
import pulumi_gcp as gcp

REGION = "us-west1"
PRODUCT_LABEL = "github_oauth_worker"
SERVICE_LABEL = "github-oauth-worker"


def labels(environment: str) -> dict[str, str]:
    """Return the common labels for resources that support labels."""
    return {
        "product": PRODUCT_LABEL,
        "service": SERVICE_LABEL,
        "environment": environment,
    }


def secret_id(environment: str, name: str) -> str:
    """Build the ID created by the owner-run bootstrap program."""
    return f"github-oauth-worker-{environment}-{name}"


def service_account_id(environment: str, role: str) -> str:
    """Match bootstrap account IDs within GCP's 30-character limit."""
    environment_id = "prod" if environment == "production" else environment
    return f"gh-oauth-worker-{environment_id}-{role}"


config = pulumi.Config()
gcp_config = pulumi.Config("gcp")
environment = config.require("environment")
if environment not in {"test", "production"}:
    raise pulumi.RunError("environment must be either 'test' or 'production'.")
if pulumi.get_stack() != environment:
    raise pulumi.RunError("The Pulumi stack name must match the configured environment.")

project_id = gcp_config.require("project")
cms_minimum_engine_version = config.require("cmsMinimumEngineVersion")
cms_allowed_engine_branches = config.require("cmsAllowedEngineBranches")

service_mode = config.get("serviceMode") or "bootstrap"
if service_mode not in {"bootstrap", "ready"}:
    raise pulumi.RunError("serviceMode must be either 'bootstrap' or 'ready'.")

runtime_service_account = (
    f"{service_account_id(environment, 'runtime')}@{project_id}.iam.gserviceaccount.com"
)
ci_service_account = f"{service_account_id(environment, 'ci')}@{project_id}.iam.gserviceaccount.com"
artifact_registry_host = f"{REGION}-docker.pkg.dev"
image = docker.Image(
    f"github-oauth-worker-{environment}-image",
    image_name=(
        f"{artifact_registry_host}/{project_id}/github-oauth-worker-{environment}/worker:current"
    ),
    build=docker.DockerBuildArgs(
        context="..",
        dockerfile="../Dockerfile",
        platform="linux/amd64",
        # This provider's BuildKit path emits invalid tar headers on Windows; BuilderV1 succeeds.
        builder_version=docker.BuilderVersion.BUILDER_V1,
    ),
    build_on_preview=False,
    registry=docker.RegistryArgs(
        server=artifact_registry_host,
        username="oauth2accesstoken",
        password=pulumi.Output.secret(gcp.organizations.get_client_config_output().access_token),
    ),
)
environment_variables = [
    gcp.cloudrunv2.ServiceTemplateContainerEnvArgs(name="SERVICE_MODE", value=service_mode),
    gcp.cloudrunv2.ServiceTemplateContainerEnvArgs(name="ENVIRONMENT", value=environment),
    gcp.cloudrunv2.ServiceTemplateContainerEnvArgs(name="GCP_PROJECT_ID", value=project_id),
    gcp.cloudrunv2.ServiceTemplateContainerEnvArgs(
        name="ACCESS_POLICY", value=config.get("accessPolicy") or "github_login_whitelist"
    ),
    gcp.cloudrunv2.ServiceTemplateContainerEnvArgs(
        name="GITHUB_LOGIN_WHITELIST", value=config.get("githubLoginWhitelist") or ""
    ),
    gcp.cloudrunv2.ServiceTemplateContainerEnvArgs(
        name="CMS_MINIMUM_ENGINE_VERSION", value=cms_minimum_engine_version
    ),
    gcp.cloudrunv2.ServiceTemplateContainerEnvArgs(
        name="CMS_ALLOWED_ENGINE_BRANCHES", value=cms_allowed_engine_branches
    ),
]

if service_mode == "ready":
    public_base_url = config.require("publicBaseUrl")
    github_app_client_id = config.require("githubAppClientId")
    github_app_slug = config.get("githubAppSlug")
    github_app_client_secret = config.require_secret("githubAppClientSecret")
    state_signing_secret = config.require_secret("stateSigningSecret")
    github_secret_id = secret_id(environment, "github-app-client-secret")
    state_secret_id = secret_id(environment, "state-signing-secret")
    gcp.secretmanager.SecretVersion(
        f"{environment}-github-app-client-secret-version",
        project=project_id,
        secret=f"projects/{project_id}/secrets/{github_secret_id}",
        secret_data=github_app_client_secret,
        deletion_policy="DISABLE",
    )
    gcp.secretmanager.SecretVersion(
        f"{environment}-state-signing-secret-version",
        project=project_id,
        secret=f"projects/{project_id}/secrets/{state_secret_id}",
        secret_data=state_signing_secret,
        deletion_policy="DISABLE",
    )
    environment_variables.extend(
        [
            gcp.cloudrunv2.ServiceTemplateContainerEnvArgs(
                name="PUBLIC_BASE_URL", value=public_base_url
            ),
            gcp.cloudrunv2.ServiceTemplateContainerEnvArgs(
                name="GITHUB_APP_CLIENT_ID", value=github_app_client_id
            ),
            gcp.cloudrunv2.ServiceTemplateContainerEnvArgs(
                name="GITHUB_APP_CLIENT_SECRET",
                value_source=gcp.cloudrunv2.ServiceTemplateContainerEnvValueSourceArgs(
                    secret_key_ref=(
                        gcp.cloudrunv2.ServiceTemplateContainerEnvValueSourceSecretKeyRefArgs(
                            secret=github_secret_id,
                            version="latest",
                        )
                    ),
                ),
            ),
            gcp.cloudrunv2.ServiceTemplateContainerEnvArgs(
                name="STATE_SIGNING_SECRET",
                value_source=gcp.cloudrunv2.ServiceTemplateContainerEnvValueSourceArgs(
                    secret_key_ref=(
                        gcp.cloudrunv2.ServiceTemplateContainerEnvValueSourceSecretKeyRefArgs(
                            secret=state_secret_id,
                            version="latest",
                        )
                    ),
                ),
            ),
        ]
    )
    if github_app_slug:
        environment_variables.append(
            gcp.cloudrunv2.ServiceTemplateContainerEnvArgs(
                name="GITHUB_APP_SLUG", value=github_app_slug
            )
        )

service_name = f"github-oauth-worker-{environment}"
service = gcp.cloudrunv2.Service(
    service_name,
    project=project_id,
    location=REGION,
    name=service_name,
    ingress="INGRESS_TRAFFIC_ALL",
    labels=labels(environment),
    template=gcp.cloudrunv2.ServiceTemplateArgs(
        labels=labels(environment),
        service_account=runtime_service_account,
        scaling=gcp.cloudrunv2.ServiceTemplateScalingArgs(min_instance_count=0),
        containers=[
            gcp.cloudrunv2.ServiceTemplateContainerArgs(
                image=image.repo_digest,
                envs=environment_variables,
                resources=gcp.cloudrunv2.ServiceTemplateContainerResourcesArgs(
                    limits={"cpu": "1", "memory": "512Mi"},
                ),
            )
        ],
    ),
)
public_invoker = gcp.cloudrunv2.ServiceIamMember(
    f"{service_name}-public-invoker",
    project=project_id,
    location=REGION,
    name=service.name,
    role="roles/run.invoker",
    member="allUsers",
)
gcp.cloudrunv2.ServiceIamMember(
    f"{service_name}-ci-admin",
    project=project_id,
    location=REGION,
    name=service.name,
    role="roles/run.admin",
    member=f"serviceAccount:{ci_service_account}",
    opts=pulumi.ResourceOptions(depends_on=[public_invoker]),
)

pulumi.export("service_url", service.uri)
