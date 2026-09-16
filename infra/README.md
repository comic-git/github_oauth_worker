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

Create GitHub Environments named `test` and `production`. Each environment holds the same
non-secret variable names, with values from its own bootstrap outputs:

| Environment  | Variable              | Bootstrap output                |
|--------------|-----------------------|---------------------------------|
| `test`       | `GCP_WIF_PROVIDER`    | `test_wif_provider`             |
| `test`       | `GCP_SERVICE_ACCOUNT` | `test_ci_service_account`       |
| `production` | `GCP_WIF_PROVIDER`    | `production_wif_provider`       |
| `production` | `GCP_SERVICE_ACCOUNT` | `production_ci_service_account` |

Configure the `production` environment to allow only `master` deployments and require reviewers
before the future production apply job. Test does not require environment approval while the MVP
deploys test changes from every branch.

Set these shared non-secret GitHub Actions repository variables:

| Variable                  | Value                                       |
|---------------------------|---------------------------------------------|
| `GCP_PROJECT_ID`          | The GCP project ID in the bootstrap stack.  |
| `PULUMI_BACKEND_URL`      | The `gs://<bucket-name>` state-backend URL. |
| `PULUMI_SECRETS_PROVIDER` | `pulumi_secrets_provider_url` output.       |

Do not put service-account keys, Pulumi access tokens, application secrets, or the temporary
bootstrap passphrase in repository variables. Future environment-specific application secrets
belong in GitHub Environment secrets.

## Bootstrap Resources

The owner-run bootstrap program creates the required APIs, protected KMS key, separate test and
production Firestore databases, Artifact Registry repositories, Secret Manager containers, and
runtime/CI service accounts. It also creates separate GitHub Actions Workload Identity Federation
pools and providers. The test provider permits this repository on any branch; the production
provider additionally requires `master`.

Each CI identity can deploy only its matching Cloud Run service, write only its matching Artifact
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

Normal local development requires no GCP credentials. Any preview or apply requires an explicit human review. CI will later provide Docker and the same short-lived GCP identity used by Pulumi.

## Optional Cost Controls

Cloud Billing budgets and alert recipients are operator-managed GCP policy, intentionally outside
this infrastructure program. Configure them directly in Google Cloud when they are useful for a
deployment.
