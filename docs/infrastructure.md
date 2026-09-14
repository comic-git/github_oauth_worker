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
| Secret Manager           | Holds environment-specific GitHub client and state-signing secrets.                          |
| Service Accounts and IAM | Limits each worker to its own Firestore database and required secrets.                       |
| Cloud Billing budget     | Alerts the operator before unexpected spending.                                              |

## Infrastructure as Code

Pulumi Python will live under `infra/`. It creates labeled GCP resources, separate test and production Firestore Native databases, and database-scoped runtime IAM. Both stacks use the same explicit GCP project while naming their environment, public worker URL, GitHub App configuration, Firestore database ID, and origin grace period. Production uses Firestore's `(default)` database; test uses the named `test` database so test experiments cannot touch production bindings.

The initial Cloud Run revision uses explicit bootstrap mode and exposes no OAuth operation until credentials and Firestore configuration are ready. Applying Pulumi or deploying requires human confirmation and a reviewed preview.

## Networking and Data

Cloud Run allows unauthenticated ingress because Decap opens it in a browser. It exposes only worker endpoints and has no VPC, database connection pool, queue, or webhook for the MVP. Firestore is accessed only by Cloud Run's service account; database-specific IAM conditions limit each service account to its own environment database. No browser receives Firebase configuration or direct datastore access.

Firestore is chosen over Cloud SQL because binding operations are low-volume direct document reads/writes and do not need relational queries or an always-running database. Test and production database separation lets development safely exercise persistent state without affecting production bindings. The worker keeps no token in Firestore.

## Labels

Every label-capable resource carries `product=github_oauth_worker`, plus `service=github-oauth-worker` and `environment=test` or `environment=production`. The Cloud Run service names are `github-oauth-worker-test` and `github-oauth-worker-production`.

## Secrets

The deployer generates a GitHub App client secret in GitHub settings and provides it, together with a state-signing secret, through Pulumi secret configuration or Secret Manager. Never place secrets in a registration checklist, `.env.example`, command history, source, Pulumi non-secret configuration, logs, test fixtures, or browser errors.
