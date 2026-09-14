<!-- ai-agent-toolkit:managed version="1.1.0" -->
<!-- Audience: AI agents working on infrastructure or deployment, and developers. -->

# Infrastructure

## Environments

| Environment | Purpose                                                                      | Access                                     |
|-------------|------------------------------------------------------------------------------|--------------------------------------------|
| Local       | Offline unit and HTTP testing with simulated GitHub and Firestore boundaries | Developer machine.                         |
| Sandbox     | Manual App installation, enrollment, Decap, and migration verification       | Separate GCP stack/project and test App.   |
| Production  | Shared self-service OAuth worker                                             | Public Cloud Run URL; operator controlled. |

## Cloud Services

| Service                 | Purpose                                                             |
|-------------------------|---------------------------------------------------------------------|
| Cloud Run               | Public stateless FastAPI service with scale-to-zero.                |
| Firestore Native        | Repository/origin bindings and limited non-secret enrollment state. |
| Artifact Registry       | Stores the worker container image.                                  |
| Secret Manager          | Holds the GitHub client secret and state-signing secret.            |
| Service Account and IAM | Limits runtime access to Firestore and only its required secrets.   |
| Cloud Billing budget    | Alerts the operator before unexpected spending.                     |

## Infrastructure as Code

Pulumi Python will live under `infra/`. It creates GCP resources, the default Firestore Native database, and runtime IAM, but it does not create a GitHub App or commit generated credentials. Each environment is a separate stack with explicit project, region, public worker URL, policy configuration, and origin grace period.

The initial Cloud Run revision uses explicit bootstrap mode and exposes no OAuth operation until credentials and Firestore configuration are ready. Applying Pulumi or deploying requires human confirmation and a reviewed preview.

## Networking and Data

Cloud Run allows unauthenticated ingress because Decap opens it in a browser. It exposes only worker endpoints and has no VPC, database connection pool, queue, or webhook for the MVP. Firestore is accessed only by Cloud Run's service account; no browser receives Firebase configuration or direct datastore access.

Firestore is chosen over Cloud SQL because binding operations are low-volume direct document reads/writes and do not need relational queries or an always-running database. The worker keeps no token in Firestore.

## Secrets

The deployer generates a GitHub App client secret in GitHub settings and provides it, together with a state-signing secret, through Pulumi secret configuration or Secret Manager. Never place secrets in a registration checklist, `.env.example`, command history, source, Pulumi non-secret configuration, logs, test fixtures, or browser errors.
