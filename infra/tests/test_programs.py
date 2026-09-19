"""Offline structural checks for both Pulumi programs."""

import asyncio
import runpy
import unittest
from pathlib import Path

import pulumi
from pulumi.runtime import (
    MockCallArgs,
    MockResourceArgs,
    Mocks,
    set_mocks,
    test,
)
from pulumi.runtime import config as runtime_config

INFRA_DIRECTORY = Path(__file__).resolve().parents[1]


class ResourceMocks(Mocks):
    """Build Pulumi resource graphs without a GCP account or provider calls."""

    def __init__(self) -> None:
        self.resource_types: list[str] = []
        self.resource_inputs: dict[str, dict[str, object]] = {}

    def call(self, args: MockCallArgs) -> tuple[dict[str, object], list[tuple[str, str]]]:
        if args.token == "gcp:organizations/getClientConfig:getClientConfig":
            return {"accessToken": "test-access-token"}, []
        if args.token == "gcp:organizations/getProject:getProject":
            return {"number": "123456789"}, []
        return {}, []

    def new_resource(self, args: MockResourceArgs) -> tuple[str, dict[str, object]]:
        self.resource_types.append(args.typ)
        outputs = dict(args.inputs)
        self.resource_inputs[args.name] = outputs
        outputs.setdefault("email", f"{args.name}@example.test")
        outputs.setdefault("name", args.name)
        outputs.setdefault(
            "repoDigest",
            "us-west1-docker.pkg.dev/comic-git/test/worker@sha256:" + "a" * 64,
        )
        outputs.setdefault("uri", f"https://{args.name}.example.test")
        return args.name, outputs


class PulumiProgramTests(unittest.TestCase):
    """Ensure provider input changes fail locally before an owner runs a preview."""

    def test_main_program_builds_a_bootstrap_service(self) -> None:
        mocks = self._run_program(
            program_path=INFRA_DIRECTORY / "__main__.py",
            project_name="github-oauth-worker-infra",
            stack_name="test",
            configuration={
                "github-oauth-worker-infra:environment": "test",
                "gcp:project": "comic-git",
                "github-oauth-worker-infra:firestoreDatabaseId": "test",
                "github-oauth-worker-infra:cmsMinimumEngineVersion": "1.2",
                "github-oauth-worker-infra:cmsAllowedEngineBranches": "latest,master,cms",
            },
        )
        self.assertIn("docker:index/image:Image", mocks.resource_types)
        image_inputs = mocks.resource_inputs["github-oauth-worker-test-image"]
        self.assertEqual(
            image_inputs["imageName"],
            "us-west1-docker.pkg.dev/comic-git/github-oauth-worker-test/worker:current",
        )
        self.assertEqual(image_inputs["build"]["context"], "..")
        self.assertEqual(image_inputs["build"]["dockerfile"], r"..\Dockerfile")
        self.assertFalse(image_inputs["buildOnPreview"])
        self.assertIn("gcp:cloudrunv2/service:Service", mocks.resource_types)
        self.assertIn("gcp:cloudrunv2/serviceIamMember:ServiceIamMember", mocks.resource_types)

    def test_main_program_builds_a_ready_service_with_secret_versions(self) -> None:
        mocks = self._run_program(
            program_path=INFRA_DIRECTORY / "__main__.py",
            project_name="github-oauth-worker-infra",
            stack_name="test",
            configuration={
                "github-oauth-worker-infra:environment": "test",
                "gcp:project": "comic-git",
                "github-oauth-worker-infra:firestoreDatabaseId": "test",
                "github-oauth-worker-infra:cmsMinimumEngineVersion": "1.2",
                "github-oauth-worker-infra:cmsAllowedEngineBranches": "latest,master,cms",
                "github-oauth-worker-infra:serviceMode": "ready",
                "github-oauth-worker-infra:publicBaseUrl": "https://worker.example.test",
                "github-oauth-worker-infra:githubAppClientId": "client-id",
                "github-oauth-worker-infra:githubAppClientSecret": "client-secret",
                "github-oauth-worker-infra:stateSigningSecret": "state-secret",
            },
            secret_keys=[
                "github-oauth-worker-infra:githubAppClientSecret",
                "github-oauth-worker-infra:stateSigningSecret",
            ],
        )
        self.assertEqual(
            mocks.resource_types.count("gcp:secretmanager/secretVersion:SecretVersion"),
            2,
        )

    def test_bootstrap_program_builds_environment_boundaries(self) -> None:
        mocks = self._run_program(
            program_path=INFRA_DIRECTORY / "bootstrap" / "__main__.py",
            project_name="github-oauth-worker-bootstrap",
            stack_name="bootstrap",
            configuration={
                "gcp:project": "comic-git",
                "github-oauth-worker-bootstrap:pulumiStateBucket": "pulumi-state-test",
            },
        )
        self.assertIn("gcp:firestore/database:Database", mocks.resource_types)
        self.assertIn("gcp:kms/keyRing:KeyRing", mocks.resource_types)
        self.assertIn("gcp:kms/cryptoKey:CryptoKey", mocks.resource_types)
        self.assertEqual(
            mocks.resource_types.count("gcp:kms/cryptoKeyIAMMember:CryptoKeyIAMMember"),
            2,
        )
        self.assertEqual(
            mocks.resource_types.count("gcp:storage/bucketIAMMember:BucketIAMMember"),
            2,
        )
        for resource_name in (
            "github-oauth-worker-test-runtime",
            "github-oauth-worker-test-ci",
            "github-oauth-worker-production-runtime",
            "github-oauth-worker-production-ci",
        ):
            account_id = mocks.resource_inputs[resource_name]["accountId"]
            self.assertLessEqual(len(str(account_id)), 30)
        for resource_name in (
            "github-oauth-worker-test-pool",
            "github-oauth-worker-production-pool",
        ):
            pool_id = mocks.resource_inputs[resource_name]["workloadIdentityPoolId"]
            self.assertLessEqual(len(str(pool_id)), 32)
        self.assertIn(
            "gcp:iam/workloadIdentityPoolProvider:WorkloadIdentityPoolProvider",
            mocks.resource_types,
        )

    def _run_program(
        self,
        *,
        program_path: Path,
        project_name: str,
        stack_name: str,
        configuration: dict[str, str],
        secret_keys: list[str] | None = None,
    ) -> ResourceMocks:
        mocks = ResourceMocks()
        asyncio.set_event_loop(asyncio.new_event_loop())
        set_mocks(mocks, project=project_name, stack=stack_name)
        runtime_config.set_all_config(configuration, secret_keys=secret_keys)

        @test
        def validate() -> pulumi.Output[bool]:
            runpy.run_path(str(program_path))
            return pulumi.Output.from_input(True)

        validate()
        return mocks


if __name__ == "__main__":
    unittest.main()
