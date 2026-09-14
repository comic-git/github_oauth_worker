<!-- ai-agent-toolkit:managed version="1.2.0" -->
<!-- Audience: AI agents working on infrastructure or deployment, and developers. -->

# Infrastructure

## Environments

| Environment | Purpose | Access |
|---|---|---|
| Local | Offline unit and HTTP testing with simulated GitHub and Firestore boundaries | Developer machine. |
| Test | Manual App installation, enrollment, Decap, and migration verification | Dedicated worker, App, service account, and Firestore database in the shared GCP project. |
| Production | Shared self-service OAuth worker | Dedicated worker, App, service account, and Firestore database in the shared GCP project. |

## Cloud Services

| Service | Purpose |
|---|---|
| Cloud Run | Separate public stateless FastAPI workers with scale-to-zero. |
| Firestore Native | Separate test and production databases for bindings and limited non-secret enrollment state. |
| Artifact Registry | Stores worker deployment artifacts temporarily. A cleanup policy deletes every image version more than 30 days old; deployed Cloud Run revisions retain their imported image. Every revision-creating deployment first publishes a fresh immutable image digest, so it never depends on a cleaned-up artifact. |
| Secret Manager | Holds environment-specific GitHub client and state-signing secrets. |
| Service Accounts and IAM | Limits each worker to its own Firestore database and required secrets. |
| Cloud Billing budget | Alerts the operator before unexpected spending. |

## Infrastructure as Code

Pulumi Python will live under `infra/`. It creates labeled GCP resources in `us-west1`, separate test and production Firestore Native databases, database-scoped runtime IAM, and an Artifact Registry cleanup policy that deletes image versions older than 30 days. Both stacks use the same explicit GCP project while naming their environment, public worker URL, GitHub App configuration, Firestore database ID, and origin grace period. Production uses Firestore's `(default)` database; test uses the named `test` database so test experiments cannot touch production bindings.

`infra/bootstrap/` is a separate, owner-run Pulumi program. Its one-time local bootstrap creates the required GCP APIs, GitHub Actions Workload Identity Federation providers, and environment-specific CI service accounts. The main infrastructure program never manages the bootstrap identity path, so an ordinary CI deployment cannot remove its own ability to authenticate. GitHub Actions uses short-lived OIDC credentials through Workload Identity Federation, never a stored GCP service-account key. The test provider accepts only `comic-git/github_oauth_worker`; the production provider additionally restricts authentication to `master`. Both providers impersonate only their respective CI service account.

Before bootstrap, the owner manually creates a private GCS Pulumi-state bucket in `us-west1` and enables GCP billing. This bucket is intentionally outside Pulumi management because it is the durable state substrate required to run Pulumi. After `pulumi login gs://...`, the owner runs the bootstrap stack locally with personal GCP credentials. Bootstrap outputs the test and production workload-identity-provider resource names and CI service-account emails; those non-secret values become GitHub repository variables. Main test and production stacks use the same GCS state backend and never need a Pulumi access token or a GCP service-account key in GitHub.

Bootstrap creates one test and one production deployer service account plus a separate runtime service account for each worker. It scopes the test deployer to test-named resources and the test Firestore database wherever IAM supports resource conditions; the production deployer receives only production-scoped permissions. The WIF providers, their impersonation grants, and CI deployer privileges remain bootstrap-owned. Main infrastructure owns application resources, runtime service accounts, runtime IAM, and secret bindings.

The initial Cloud Run revision uses explicit bootstrap mode and exposes no OAuth operation until credentials and Firestore configuration are ready. Applying Pulumi or deploying requires human confirmation and a reviewed preview.

## Networking and Data

Cloud Run allows unauthenticated ingress because Decap opens it in a browser. It exposes only worker endpoints and has no VPC, database connection pool, queue, or webhook for the MVP. Firestore is accessed only by Cloud Run's service account; database-specific IAM conditions limit each service account to its own environment database. No browser receives Firebase configuration or direct datastore access.

Firestore is chosen over Cloud SQL because binding operations are low-volume direct document reads/writes and do not need relational queries or an always-running database. Test and production database separation lets development safely exercise persistent state without affecting production bindings. The worker keeps no token in Firestore.

## Labels

Every label-capable resource carries `product=github_oauth_worker`, plus `service=github-oauth-worker` and `environment=test` or `environment=production`. The Cloud Run service names are `github-oauth-worker-test` and `github-oauth-worker-production`.

## Secrets

The deployer generates a GitHub App client secret in GitHub settings and provides it, together with a state-signing secret, through Pulumi secret configuration or Secret Manager. Never place secrets in a registration checklist, `.env.example`, command history, source, Pulumi non-secret configuration, logs, test fixtures, or browser errors.
