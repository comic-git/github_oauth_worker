<!-- ai-agent-toolkit:managed version="1.1.0" -->
<!-- Audience: AI agents working on infrastructure or deployment, and developers. -->

# Infrastructure

## Environments

| Environment | Purpose                                                                      | Access                                                                                    |
|-------------|------------------------------------------------------------------------------|-------------------------------------------------------------------------------------------|
| Local       | Offline unit and HTTP testing with simulated GitHub and Firestore boundaries | Developer machine.                                                                        |
| Test        | Manual App installation, enrollment, Decap, and migration verification       | Dedicated worker, App, service account, and Firestore database in the shared GCP project. |
| Production  | Shared self-service OAuth worker                                             | Dedicated worker, App, service account, and Firestore database in the shared GCP project. |

## Cloud Services

| Service                  | Purpose                                                                                      |
|--------------------------|----------------------------------------------------------------------------------------------|
| Cloud Run                | Separate public stateless FastAPI workers with scale-to-zero.                                |
| Firestore Native         | Separate test and production databases for bindings and limited non-secret enrollment state. |
| Artifact Registry        | Stores worker deployment artifacts.                                                          |
| Cloud Storage            | Holds versioned, private Pulumi state outside the managed resource graph.                    |
| Secret Manager           | Holds environment-specific GitHub client and state-signing secrets.                          |
| Cloud KMS                | Encrypts Pulumi secret configuration and secret-marked state values.                         |
| Service Accounts and IAM | Limits each worker to its own Firestore database and required secrets.                       |

## Infrastructure as Code

Pulumi Python lives under `infra/`. A manually created, labeled, versioned private Cloud Storage bucket holds Pulumi state because it is the substrate needed to manage all other resources. The owner-run bootstrap stack creates stable security boundaries: a protected KMS key for Pulumi secrets, labeled Artifact Registry repositories, separate test and production Firestore Native databases, runtime/CI identities, Secret Manager containers, database-scoped runtime IAM, bucket-scoped state access for CI, and repository-restricted WIF providers. The normal test and production stacks deploy only a Cloud Run revision and optional Secret Manager versions. This prevents test CI from needing project-wide IAM authority in the shared project. Production uses Firestore's `(default)` database; test uses the named `test` database so test experiments cannot touch production bindings.

GitHub Actions uses `test` and `production` GitHub Environments. Each environment provides the same
`GCP_WIF_PROVIDER` and `GCP_SERVICE_ACCOUNT` variable names with environment-specific values. The
shared project ID, state-backend URL, and KMS secrets-provider URL are repository variables.
Production is restricted to `master` and requires environment review before a production apply.

The initial Cloud Run revision uses explicit bootstrap mode and exposes no OAuth operation until credentials and Firestore configuration are ready. Applying Pulumi or deploying requires human confirmation and a reviewed preview.

## Networking and Data

Cloud Run allows unauthenticated ingress because Decap opens it in a browser. It exposes only worker endpoints and has no VPC, database connection pool, queue, or webhook for the MVP. Firestore is accessed only by Cloud Run's service account; database-specific IAM conditions limit each service account to its own environment database. No browser receives Firebase configuration or direct datastore access.

Firestore is chosen over Cloud SQL because binding operations are low-volume direct document reads/writes and do not need relational queries or an always-running database. Test and production database separation lets development safely exercise persistent state without affecting production bindings. The worker keeps no token in Firestore.

## Labels

Every label-capable resource carries `product=github_oauth_worker`, plus `service=github-oauth-worker` and the matching environment label. The manually created Pulumi state bucket and KMS key are shared by both environments and use `environment=shared`; all other resources use `environment=test` or `environment=production`. The Cloud Run service names are `github-oauth-worker-test` and `github-oauth-worker-production`.

## Secrets

The deployer generates a GitHub App client secret in GitHub settings and provides it, together with a state-signing secret, through Pulumi secret configuration or Secret Manager. The bootstrap stack is initialized once with a temporary passphrase because it creates the KMS key that subsequently encrypts Pulumi secret configuration. The owner immediately migrates that stack to the KMS provider; normal stacks use the KMS provider from initialization. CI has encrypt/decrypt access to this one key, while Cloud Run has no KMS permission. Never place secrets in a registration checklist, `.env.example`, command history, source, Pulumi non-secret configuration, logs, test fixtures, or browser errors.
