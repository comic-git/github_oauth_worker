# Infrastructure Bootstrap

## Owner Bootstrap Procedure

Complete this procedure once as the `comic-git` GCP project owner. It creates the stable foundation
for both environments. It does not deploy a Cloud Run revision, create a GitHub App, expose the
OAuth worker publicly, or configure optional billing alerts.

1. In Google Cloud Console, select the `comic-git` project and open **Cloud Storage > Buckets**.
2. Select **Create** and give the bucket a globally unique name, such as
   `comic-git-pulumi-state-<unique-suffix>`.
3. Choose **Region** and select `us-west1`.
4. Choose the `Standard` storage class, **Uniform** access control, and **Enforced** public access
   prevention. Add `product=github_oauth_worker`, `service=github-oauth-worker`, and
   `environment=shared` labels. Do not enable a retention policy or retention lock.
5. Create the bucket. From its **Protection** tab, enable object versioning and leave soft delete
   enabled. Record its URL as `gs://<bucket-name>`.
6. Authenticate the local Google Cloud SDK for Pulumi's Application Default Credentials:

   ```powershell
   gcloud auth application-default login
   ```

7. Create the isolated Pulumi tooling environment from the repository root, if it does not already
   exist:

   ```powershell
   py -3.14 -m venv infra\.venv
   .\infra\.venv\Scripts\python.exe -m pip install -r infra\requirements.txt
   ```

8. Log Pulumi into the bucket and select the bootstrap directory:

   ```powershell
   Copy-Item Pulumi.bootstrap.example.yaml Pulumi.bootstrap.yaml
   pulumi login gs://<bucket-name>
   Set-Location infra\bootstrap
   pulumi stack init bootstrap --secrets-provider=passphrase
   ```

   If the `bootstrap` stack already exists, use `pulumi stack select bootstrap` instead of
   initializing it again. Do not overwrite an existing `Pulumi.bootstrap.yaml`; it holds local,
   deployment-specific configuration and is intentionally ignored by Git. The passphrase is
   temporary: the first bootstrap update creates the project's KMS key, which cannot encrypt the
   stack before it exists. Generate a strong unique passphrase in a password manager. Do not commit
   or paste it into a command.

9. Set the owner-only, non-secret bootstrap configuration. `pulumiStateBucket` is the bare bucket
   name, not its `gs://` URL. The bootstrap program grants the future test and production CI
   identities access to this exact bucket:

   ```powershell
   pulumi config set pulumiStateBucket <bucket-name>
   ```

10. Inspect the proposed infrastructure, then apply it only after reviewing the full preview:

    ```powershell
    pulumi preview
    pulumi up
    ```

11. Migrate the bootstrap stack to the KMS key it just created, then record the output URL for the
    normal stacks and later CI configuration:

    ```powershell
    $kmsProvider = pulumi stack output pulumi_secrets_provider_url
    pulumi stack change-secrets-provider $kmsProvider
    ```

    Keep the original passphrase in the password manager until the migration has completed and a
    subsequent `pulumi preview` succeeds. It is never a GitHub Actions secret.

12. Record the non-secret `pulumi_secrets_provider_url`, `test_wif_provider`,
    `test_ci_service_account`, `production_wif_provider`, and `production_ci_service_account`
    outputs. They are the GitHub Actions repository-variable values needed by the later CI setup.

At this point the GCP foundation exists, but neither Cloud Run worker has been deployed. The next
step is to add CI that builds an immutable container image, pushes it to the environment's Artifact
Registry repository, and applies the matching normal Pulumi stack.

## State Backend

Before running either Pulumi program, the owner manually creates one private GCS bucket in
`us-west1` for Pulumi state. This bucket is intentionally outside Pulumi management: it is the
state substrate required to manage every other resource.

The bucket must use uniform bucket-level access, public access prevention, versioning, and the
shared product/service/environment labels listed above. Record its non-secret backend URL as
`gs://<bucket-name>` and configure it locally with `pulumi login gs://<bucket-name>`. The bootstrap
stack separately receives the bare bucket name as `pulumiStateBucket` so it can grant the two CI
identities `roles/storage.objectAdmin` on that one bucket. Later CI will use the same backend URL
from a non-secret `PULUMI_BACKEND_URL` repository variable. Do not put service-account keys, Pulumi
access tokens, or application secrets in GitHub repository variables.

Create an isolated tooling environment before running either stack:

```powershell
py -3.14 -m venv infra\.venv
.\infra\.venv\Scripts\python.exe -m pip install -r infra\requirements.txt
```

## Bootstrap Program

Run `infra/bootstrap/` locally as the GCP project owner. It creates the Google APIs, a protected
Cloud KMS key for Pulumi secret configuration, Firestore databases, Artifact Registry repositories,
Secret Manager containers, GitHub Actions Workload Identity Federation pools/providers, and
separate test and production runtime/CI service accounts. The bootstrap program also grants
narrowly scoped runtime and CI permissions. This keeps test CI from requiring project-wide IAM
authority in the shared project.

Before its reviewed preview, set this local, non-secret bootstrap value:

```powershell
pulumi config set pulumiStateBucket <bucket-name>
```

Then run `pulumi preview` and, after reviewing it, `pulumi up` from `infra\bootstrap`. This is an
owner-only operation. It must complete before a normal environment stack is deployed.

It exports non-secret values for the later GitHub Actions repository variables:

| GitHub Actions variable | Source |
|---|---|
| `GCP_PROJECT_ID` | Bootstrap `projectId` configuration value. |
| `GCP_WIF_PROVIDER_TEST` | `test_wif_provider` output. |
| `GCP_WIF_PROVIDER_PRODUCTION` | `production_wif_provider` output. |
| `GCP_SERVICE_ACCOUNT_TEST` | `test_ci_service_account` output. |
| `GCP_SERVICE_ACCOUNT_PRODUCTION` | `production_ci_service_account` output. |

It also exports `pulumi_secrets_provider_url`. Initialize the normal stacks with that provider,
for example `pulumi stack init test --secrets-provider=<pulumi_secrets_provider_url>`. The two CI
identities receive `roles/cloudkms.cryptoKeyEncrypterDecrypter` on that exact key so they can read
existing Pulumi secrets and encrypt newly supplied ones. The Cloud Run identities have no Cloud KMS
permission; they read application secrets only through Secret Manager. The CI identities also have
`roles/storage.objectAdmin` only on the configured Pulumi state bucket.

The test provider accepts GitHub OIDC from `comic-git/github_oauth_worker`. The production provider
adds a `master` branch restriction. Each provider is in a separate pool so a test identity cannot
impersonate the production deployment identity.

## Optional Cost Controls

Cloud Billing budgets and alert recipients are intentionally outside this bootstrap program. They
are operator-managed project policy rather than a requirement for the OAuth worker. Configure them
directly in Google Cloud when they are useful for a deployment.

## Main Stacks

`infra/` has one stack per environment. Each stack requires an immutable container image digest and
the same explicit GCP project. Its first deployment uses `serviceMode=bootstrap`, so it needs no
GitHub credentials. The resulting `service_url` is the stable URL used to register the matching
GitHub App. A later `serviceMode=ready` update requires the public URL, App client ID, and two
Pulumi secret values; it creates matching Secret Manager versions and injects them into Cloud Run.

Normal local development does not need GCP credentials. Applying either program requires explicit
human review of a Pulumi preview.

## First Deployment

After bootstrap, publish an immutable image to the matching environment repository. CI will perform
this build-and-push flow once it is configured; until then, use a reviewed owner-run build. Set the
resulting digest as `imageUri`, select the `test` or `production` stack from `infra\`, and apply it
with `serviceMode=bootstrap`. The exported `service_url` becomes the matching GitHub App's callback
and setup base URL.

After registering the App, set `serviceMode=ready`, `publicBaseUrl`, and `githubAppClientId`. Add
the App client secret and state-signing secret with `pulumi config set --secret`, review the preview,
and apply. The normal stack creates the Secret Manager versions and only then injects them into the
Cloud Run revision.
