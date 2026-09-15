"""Provision the stable GCP boundaries that CI must not be able to redefine."""

import pulumi
import pulumi_gcp as gcp

REGION = "us-west1"
PRODUCT_LABEL = "github_oauth_worker"
SERVICE_LABEL = "github-oauth-worker"
GITHUB_OIDC_ISSUER = "https://token.actions.githubusercontent.com"
PULUMI_KEY_RING_NAME = "github-oauth-worker-pulumi"
PULUMI_CRYPTO_KEY_NAME = "github-oauth-worker-pulumi-secrets"


def require_bootstrap_config(config: pulumi.Config) -> tuple[str, str]:
    """Read the owner-only configuration that defines shared infrastructure boundaries."""
    repository = config.get("githubRepository") or "comic-git/github_oauth_worker"
    pulumi_state_bucket = config.require("pulumiStateBucket")
    if pulumi_state_bucket.startswith("gs://"):
        raise pulumi.RunError("pulumiStateBucket must be a bucket name, without the gs:// prefix.")
    return repository, pulumi_state_bucket


def labels(environment: str) -> dict[str, str]:
    """Return the common labels for resources that support labels."""
    return {
        "product": PRODUCT_LABEL,
        "service": SERVICE_LABEL,
        "environment": environment,
    }


def secret_id(environment: str, name: str) -> str:
    """Build stable, environment-specific Secret Manager identifiers."""
    return f"github-oauth-worker-{environment}-{name}"


def environment_id(environment: str) -> str:
    """Shorten the production name for GCP resources with compact ID limits."""
    return "prod" if environment == "production" else environment


def service_account_id(environment: str, role: str) -> str:
    """Build a stable service-account ID within GCP's 30-character limit."""
    short_environment_id = environment_id(environment)
    return f"gh-oauth-worker-{short_environment_id}-{role}"


def workload_identity_pool_id(environment: str) -> str:
    """Build a stable Workload Identity Pool ID within GCP's 32-character limit."""
    return f"gh-oauth-worker-{environment_id(environment)}-pool"


def create_project_services(project_id: str) -> list[gcp.projects.Service]:
    """Enable every API required by the bootstrap and normal deployment stacks."""
    service_names = (
        "artifactregistry.googleapis.com",
        "cloudkms.googleapis.com",
        "firestore.googleapis.com",
        "iam.googleapis.com",
        "run.googleapis.com",
        "secretmanager.googleapis.com",
        "storage.googleapis.com",
        "sts.googleapis.com",
    )
    return [
        gcp.projects.Service(
            service_name.replace(".", "-"),
            project=project_id,
            service=service_name,
            disable_on_destroy=False,
        )
        for service_name in service_names
    ]


def create_environment(
    *,
    project_id: str,
    project_number: pulumi.Output[str],
    repository: str,
    environment: str,
    service_dependencies: list[gcp.projects.Service],
    cloud_run_service_agent: pulumi.Output[str],
    pulumi_secrets_key: pulumi.Input[str],
    pulumi_state_bucket: str,
) -> dict[str, pulumi.Input[str]]:
    """Create one isolated runtime, artifact, secret, and CI identity boundary."""
    database_id = "test" if environment == "test" else "(default)"
    resource_prefix = f"github-oauth-worker-{environment}"

    runtime_service_account = gcp.serviceaccount.Account(
        f"{resource_prefix}-runtime",
        project=project_id,
        account_id=service_account_id(environment, "runtime"),
        display_name=f"GitHub OAuth worker {environment} runtime",
        opts=pulumi.ResourceOptions(depends_on=service_dependencies),
    )
    ci_service_account = gcp.serviceaccount.Account(
        f"{resource_prefix}-ci",
        project=project_id,
        account_id=service_account_id(environment, "ci"),
        display_name=f"GitHub OAuth worker {environment} deployment CI",
        opts=pulumi.ResourceOptions(depends_on=service_dependencies),
    )
    # CI writes and reads Pulumi secret configuration. Runtime only reads the
    # resulting Secret Manager values, so it receives no Cloud KMS permission.
    gcp.kms.CryptoKeyIAMMember(
        f"{resource_prefix}-ci-pulumi-secrets",
        crypto_key_id=pulumi_secrets_key,
        role="roles/cloudkms.cryptoKeyEncrypterDecrypter",
        member=ci_service_account.member,
    )
    # Pulumi's GCS backend stores state as objects; CI has no access outside
    # this manually created bucket.
    gcp.storage.BucketIAMMember(
        f"{resource_prefix}-ci-pulumi-state",
        bucket=pulumi_state_bucket,
        role="roles/storage.objectAdmin",
        member=ci_service_account.member,
    )

    database = gcp.firestore.Database(
        f"{resource_prefix}-database",
        project=project_id,
        name=database_id,
        location_id=REGION,
        type="FIRESTORE_NATIVE",
        concurrency_mode="OPTIMISTIC",
        app_engine_integration_mode="DISABLED",
        delete_protection_state="DELETE_PROTECTION_ENABLED",
        deletion_policy="PREVENT",
        opts=pulumi.ResourceOptions(depends_on=service_dependencies),
    )

    database_resource_prefix = f"projects/{project_id}/databases/{database_id}"
    gcp.projects.IAMMember(
        f"{resource_prefix}-runtime-firestore",
        project=project_id,
        role="roles/datastore.user",
        member=runtime_service_account.member,
        condition=gcp.projects.IAMMemberConditionArgs(
            title=f"{resource_prefix}-database-only",
            description="Restrict this runtime identity to its Firestore database.",
            expression=f"resource.name.startsWith('{database_resource_prefix}')",
        ),
        opts=pulumi.ResourceOptions(depends_on=[database]),
    )

    artifact_repository = gcp.artifactregistry.Repository(
        f"{resource_prefix}-images",
        project=project_id,
        location=REGION,
        repository_id=resource_prefix,
        format="DOCKER",
        description=f"Container images for the {environment} GitHub OAuth worker.",
        labels=labels(environment),
        cleanup_policies=[
            gcp.artifactregistry.RepositoryCleanupPolicyArgs(
                id="delete-images-older-than-30-days",
                action="DELETE",
                condition=gcp.artifactregistry.RepositoryCleanupPolicyConditionArgs(
                    older_than="30d",
                ),
            )
        ],
        opts=pulumi.ResourceOptions(depends_on=service_dependencies),
    )
    for identity_name, member, role in (
        ("ci-writer", ci_service_account.member, "roles/artifactregistry.writer"),
        (
            "cloud-run-reader",
            cloud_run_service_agent.apply(lambda email: f"serviceAccount:{email}"),
            "roles/artifactregistry.reader",
        ),
    ):
        gcp.artifactregistry.RepositoryIamMember(
            f"{resource_prefix}-{identity_name}",
            project=project_id,
            location=REGION,
            repository=artifact_repository.name,
            role=role,
            member=member,
        )

    for name in ("github-app-client-secret", "state-signing-secret"):
        secret = gcp.secretmanager.Secret(
            secret_id(environment, name),
            project=project_id,
            secret_id=secret_id(environment, name),
            labels=labels(environment),
            replication=gcp.secretmanager.SecretReplicationArgs(
                auto=gcp.secretmanager.SecretReplicationAutoArgs(),
            ),
            opts=pulumi.ResourceOptions(depends_on=service_dependencies),
        )
        gcp.secretmanager.SecretIamMember(
            f"{resource_prefix}-{name}-runtime-access",
            project=project_id,
            secret_id=secret.id,
            role="roles/secretmanager.secretAccessor",
            member=runtime_service_account.member,
        )
        gcp.secretmanager.SecretIamMember(
            f"{resource_prefix}-{name}-ci-version-writer",
            project=project_id,
            secret_id=secret.id,
            role="roles/secretmanager.secretVersionAdder",
            member=ci_service_account.member,
        )

    pool = gcp.iam.WorkloadIdentityPool(
        f"{resource_prefix}-pool",
        project=project_id,
        workload_identity_pool_id=workload_identity_pool_id(environment),
        display_name=f"GitHub Actions {environment}",
        description=f"GitHub Actions identities allowed to deploy {environment}.",
        opts=pulumi.ResourceOptions(depends_on=service_dependencies),
    )
    attribute_condition = f"assertion.repository == '{repository}'"
    if environment == "production":
        attribute_condition += " && assertion.ref == 'refs/heads/master'"
    provider = gcp.iam.WorkloadIdentityPoolProvider(
        f"{resource_prefix}-provider",
        project=project_id,
        workload_identity_pool_id=pool.workload_identity_pool_id,
        workload_identity_pool_provider_id="github-actions",
        display_name=f"GitHub Actions {environment}",
        attribute_condition=attribute_condition,
        attribute_mapping={
            "google.subject": "assertion.sub",
            "attribute.repository": "assertion.repository",
            "attribute.ref": "assertion.ref",
        },
        oidc=gcp.iam.WorkloadIdentityPoolProviderOidcArgs(issuer_uri=GITHUB_OIDC_ISSUER),
    )
    workload_identity_inputs = pulumi.Output.all(project_number, pool.workload_identity_pool_id)
    workload_identity_principal = workload_identity_inputs.apply(
        lambda values: (
            "principalSet://iam.googleapis.com/"
            f"projects/{values[0]}/locations/global/workloadIdentityPools/{values[1]}"
            f"/attribute.repository/{repository}"
        )
    )
    gcp.serviceaccount.IAMMember(
        f"{resource_prefix}-ci-workload-identity",
        service_account_id=ci_service_account.name,
        role="roles/iam.workloadIdentityUser",
        member=workload_identity_principal,
    )
    gcp.serviceaccount.IAMMember(
        f"{resource_prefix}-ci-act-as-runtime",
        service_account_id=runtime_service_account.name,
        role="roles/iam.serviceAccountUser",
        member=ci_service_account.member,
    )
    gcp.projects.IAMMember(
        f"{resource_prefix}-ci-cloud-run-admin",
        project=project_id,
        role="roles/run.admin",
        member=ci_service_account.member,
        condition=gcp.projects.IAMMemberConditionArgs(
            title=f"{resource_prefix}-cloud-run-only",
            description="Restrict CI to its one Cloud Run service.",
            expression=(
                "resource.name == "
                f"'projects/{project_id}/locations/{REGION}/services/{resource_prefix}'"
            ),
        ),
    )

    return {
        "artifact_repository": artifact_repository.repository_id,
        "ci_service_account": ci_service_account.email,
        "database": database.name,
        "runtime_service_account": runtime_service_account.email,
        "wif_provider": provider.name,
    }


config = pulumi.Config()
gcp_config = pulumi.Config("gcp")
project_id = gcp_config.require("project")
if config.get("region") not in (None, REGION):
    raise pulumi.RunError(f"This worker is intentionally deployed only in {REGION}.")
(
    github_repository,
    pulumi_state_bucket,
) = require_bootstrap_config(config)

project = gcp.organizations.get_project_output(project_id=project_id)
services = create_project_services(project_id)
cloud_run_identity = gcp.projects.ServiceIdentity(
    "cloud-run-service-agent",
    project=project_id,
    service="run.googleapis.com",
    opts=pulumi.ResourceOptions(depends_on=services),
)
pulumi_key_ring = gcp.kms.KeyRing(
    "pulumi-secrets-key-ring",
    project=project_id,
    location=REGION,
    name=PULUMI_KEY_RING_NAME,
    opts=pulumi.ResourceOptions(depends_on=services, protect=True),
)
pulumi_secrets_key = gcp.kms.CryptoKey(
    "pulumi-secrets-key",
    key_ring=pulumi_key_ring.id,
    name=PULUMI_CRYPTO_KEY_NAME,
    purpose="ENCRYPT_DECRYPT",
    labels=labels("shared"),
    deletion_policy="PREVENT",
    # KMS also delays any manually approved key-version destruction by 30 days.
    destroy_scheduled_duration="2592000s",
    opts=pulumi.ResourceOptions(protect=True),
)
pulumi_secrets_provider_url = (
    f"gcpkms://projects/{project_id}/locations/{REGION}/keyRings/{PULUMI_KEY_RING_NAME}"
    f"/cryptoKeys/{PULUMI_CRYPTO_KEY_NAME}"
)

test = create_environment(
    project_id=project_id,
    project_number=project.number,
    repository=github_repository,
    environment="test",
    service_dependencies=services,
    cloud_run_service_agent=cloud_run_identity.email,
    pulumi_secrets_key=pulumi_secrets_key.id,
    pulumi_state_bucket=pulumi_state_bucket,
)
production = create_environment(
    project_id=project_id,
    project_number=project.number,
    repository=github_repository,
    environment="production",
    service_dependencies=services,
    cloud_run_service_agent=cloud_run_identity.email,
    pulumi_secrets_key=pulumi_secrets_key.id,
    pulumi_state_bucket=pulumi_state_bucket,
)

for environment, values in (("test", test), ("production", production)):
    for name, value in values.items():
        pulumi.export(f"{environment}_{name}", value)
pulumi.export("pulumi_secrets_provider_url", pulumi_secrets_provider_url)
