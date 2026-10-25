# Infrastructure

This directory contains the owner-run bootstrap program and the normal Pulumi stacks for the
GitHub OAuth worker. The bootstrap program establishes the static GCP identity and data boundaries.
The normal `test` and `production` stacks later deploy Cloud Run revisions into those boundaries.

## Bootstrap

Run this procedure once as the GCP project owner. It does not deploy a Cloud Run revision, create a
GitHub App, expose the worker publicly, or configure optional billing alerts.

1. Create one private GCS bucket for Pulumi state in `us-west1`.

   - Use the Standard storage class, uniform bucket-level access, and enforced public access
     prevention.
   - Add `product=github_oauth_worker`, `service=github-oauth-worker`, and
     `environment=shared` labels.
   - Enable object versioning and leave soft delete enabled. Do not enable a retention policy or
     retention lock.
   - Record both its name, `<bucket-name>`, and backend URL, `gs://<bucket-name>`.

2. From the repository root, authenticate local Application Default Credentials, create the
   isolated infrastructure venv, and log Pulumi into the state bucket:

   ```powershell
   gcloud auth application-default login
   py -3.14 -m venv infra\.venv
   .\infra\.venv\Scripts\python.exe -m pip install -r infra\requirements.txt
   pulumi login gs://<bucket-name>
   ```

   Both Pulumi programs are configured to use `infra/.venv`; shell activation is not required.

3. Enter the bootstrap program directory and initialize the stack on its first owner run with a
   strong, password-manager-generated temporary passphrase:

   ```powershell
   Set-Location infra\bootstrap
   pulumi stack init bootstrap --secrets-provider=passphrase
   ```

   For later owner runs, select the existing stack:

   ```powershell
   pulumi stack select bootstrap
   ```

   `Pulumi.bootstrap.yaml` is version-controlled with the normal stack settings. The first bootstrap update creates the KMS key, so it cannot use that key as its own secrets provider yet. The temporary passphrase must never be committed, placed in GitHub, or pasted into a command.

4. Configure the bare state-bucket name. The bootstrap program grants the future test and
   production CI identities access only to this bucket:

   ```powershell
   pulumi config set pulumiStateBucket <bucket-name>
   ```

5. Inspect and apply the bootstrap resources only after reviewing the preview:

   ```powershell
   pulumi preview
   pulumi up
   ```

6. Migrate the bootstrap stack to the KMS key it created, then confirm the migration with another
   preview:

   ```powershell
   $kmsProvider = pulumi stack output pulumi_secrets_provider_url
   pulumi stack change-secrets-provider $kmsProvider
   pulumi preview
   ```

   Retain the temporary passphrase in the password manager until the last preview succeeds. It is
   never a GitHub Actions secret.

## GitHub Actions Configuration

After reviewing and applying the updated bootstrap stack, create four GitHub Environments.
`test` and `production` are protected apply environments. `test-preview` and
`production-preview` are unprotected so previews can run automatically. Each holds the same
non-secret variable names, with values from its own bootstrap outputs:

| Environment          | Variable              | Bootstrap output                     |
|----------------------|-----------------------|--------------------------------------|
| `test`               | `GCP_WIF_PROVIDER`    | `test_wif_provider`                  |
| `test`               | `GCP_SERVICE_ACCOUNT` | `test_ci_service_account`            |
| `test-preview`       | `GCP_WIF_PROVIDER`    | `test_wif_provider`                  |
| `test-preview`       | `GCP_SERVICE_ACCOUNT` | `test_preview_service_account`       |
| `production`         | `GCP_WIF_PROVIDER`    | `production_wif_provider`            |
| `production`         | `GCP_SERVICE_ACCOUNT` | `production_ci_service_account`      |
| `production-preview` | `GCP_WIF_PROVIDER`    | `production_wif_provider`            |
| `production-preview` | `GCP_SERVICE_ACCOUNT` | `production_preview_service_account` |

Configure the `production` environment to allow only `master` deployments. Configure required
reviewers for both `test` and `production`: automatic workflow runs may reach the apply jobs, but
neither job runs `pulumi up` before approval. Leave **Prevent self-review** off while one maintainer
is the only available reviewer. Automatic preview jobs need separate, unprotected configuration;
referencing either protected apply environment would also pause previews. The bootstrap stack binds
each service account to its matching GitHub Environment claim. Do not expose deployment identities
to pull-request code.

Set these shared non-secret GitHub Actions repository variables:

| Variable             | Value                                       |
|----------------------|---------------------------------------------|
| `GCP_PROJECT_ID`     | The GCP project ID in the bootstrap stack.  |
| `PULUMI_BACKEND_URL` | The `gs://<bucket-name>` state-backend URL. |

The stack YAML files already contain their KMS secrets-provider URL; the old
`PULUMI_SECRETS_PROVIDER` repository variable is not used by this workflow.

The `test` and `production` apply Environments must have required reviewers before enabling
this workflow. Preview jobs start automatically and will fail until the bootstrap update and
their Environment variables are in place. Confirm the first automatic previews succeed before
approving either pending apply. The `test`
Environment accepts trusted branches; restrict `production` to `master`. A stale branch head
causes an approved apply job to fail rather than deploy old code.

Do not put service-account keys, Pulumi access tokens, application secrets, or the temporary
bootstrap passphrase in repository variables. Future environment-specific application secrets
belong in GitHub Environment secrets.

## Bootstrap Resources

The owner-run bootstrap program creates the required APIs, protected KMS key, Artifact Registry repositories, Secret Manager containers, and
runtime/CI service accounts. It also creates separate GitHub Actions Workload Identity Federation
pools and providers. The test provider permits this repository on any branch; the production
provider additionally requires `master`. Preview accounts can inspect project resources and
decrypt Pulumi stack configuration, so only trusted same-repository branches and PRs run cloud
previews. They may read state and write/delete only Pulumi lock objects in the state bucket;
they cannot write stack checkpoints or change deployed resources. Fork PRs run offline tests only.

The worker and bootstrap program do not manage a database. Keep the shared Firestore API enabled in this GCP project; other services may rely on it.

Each deployment CI identity can deploy only its matching Cloud Run service, write only its matching Artifact
Registry repository and Secret Manager versions, access only the shared Pulumi state bucket, and
encrypt/decrypt Pulumi secrets with the dedicated KMS key. The Cloud Run identities have no KMS
permission and read application secrets only through Secret Manager.

## Normal Stacks

`infra/` has one normal stack per environment. It builds the repository Dockerfile, pushes the image to its matching Artifact Registry repository, and passes the resulting immutable digest to Cloud Run. The first deployment uses `serviceMode=bootstrap`, which needs no GitHub credentials. Its exported `service_url` is the stable URL used to register the corresponding GitHub App.

Initialize the normal stacks from `infra/` only when preparing their first deployment. A reviewed `pulumi preview` does not build or push an image. `pulumi up` requires a Docker daemon and performs the build, push, and Cloud Run update. Use the KMS provider exported by the completed bootstrap stack; do not reuse the temporary bootstrap passphrase:

```powershell
Set-Location ..
$kmsProvider = pulumi stack output pulumi_secrets_provider_url --stack github-oauth-worker-bootstrap/bootstrap
pulumi stack init test --secrets-provider=$kmsProvider
pulumi stack init production --secrets-provider=$kmsProvider
```

For later owner runs, select an existing normal stack instead of initializing it again. Apply the reviewed `serviceMode=bootstrap` preview to publish the worker image and deploy the service. The service URL produced by that deployment is then used during the matching GitHub App registration.

After GitHub App registration, update the stack to `serviceMode=ready` with `publicBaseUrl`,
`githubAppClientId`, and Pulumi secret values for the App client secret and state-signing secret.
That update creates Secret Manager versions and injects them into the Cloud Run revision.
Set the non-secret `githubAppSlug` to the slug in the App's GitHub public link to enable the guided `/setup` page's **Add repository access** action. Until it is set, existing App-install redirects still work, but `/setup` cannot link users to the App's installation screen.

Both normal-stack files define the CMS engine-selector policy passed to their Cloud Run revision. `cmsMinimumEngineVersion` is the lowest released `X.Y` or `X.Y.Z` engine selector the worker will accept. `cmsAllowedEngineBranches` is the comma-separated list of moving official engine branches it will accept. Keep production limited to release-quality branches such as `latest,master`; test may include controlled development branches such as `cms`.

Normal local development requires no GCP credentials. CI previews run automatically;
apply jobs wait for GitHub Environment reviewer approval. Local Pulumi applies still require
explicit human approval. GitHub-hosted apply runners provide Docker and use the short-lived
deployment identity for the matching environment.

## Optional Cost Controls

Cloud Billing budgets and alert recipients are operator-managed GCP policy, intentionally outside
this infrastructure program. Configure them directly in Google Cloud when they are useful for a
deployment.
