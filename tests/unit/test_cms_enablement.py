"""Offline tests for the CMS migration source and target execution boundary."""

import asyncio
import base64
import io
import json
import subprocess
import tarfile
from pathlib import Path
from unittest.mock import patch

import pytest
from pydantic import SecretStr

from github_oauth_worker.cms_enablement import (
    CmsEnablementError,
    EngineMigrationContract,
    MaterializedEngine,
    decode_text_blob,
    extract_official_engine_archive,
    materialize_target_snapshot,
    parse_engine_selector,
    parse_runner_plan,
    read_target_repository_state,
    run_engine_migration,
)
from github_oauth_worker.github_client import (
    GitHubGitBlob,
    GitHubGitCommit,
    GitHubGitObject,
    GitHubGitReference,
    GitHubGitSha,
    GitHubGitTree,
    GitHubGitTreeEntry,
    GitHubRepository,
    GitHubRepositoryOwner,
)

SHA_A = "a" * 40
SHA_B = "b" * 40
SHA_C = "c" * 40


def test_extract_official_engine_archive_accepts_one_bounded_regular_file_root(
    tmp_path: Path,
) -> None:
    archive = _archive(
        {
            "comic_git_engine-cms/cms_migration_contract.json": json.dumps(
                {
                    "protocol_version": 1,
                    "runner_module": "build.migration.runner",
                    "required_runtime_packages": ["tomli-w"],
                }
            ),
            "comic_git_engine-cms/src/build/migration/runner.py": "",
        }
    )

    engine = extract_official_engine_archive(archive, tmp_path)

    assert engine.root == tmp_path / "comic_git_engine-cms"
    assert engine.contract.required_runtime_packages == ("tomli-w",)


def test_extract_official_engine_archive_rejects_unsafe_paths_and_links(tmp_path: Path) -> None:
    with pytest.raises(CmsEnablementError):
        extract_official_engine_archive(_archive({"root/..\\escape.txt": "no"}), tmp_path)

    link = tarfile.TarInfo("root/link")
    link.type = tarfile.SYMTYPE
    link.linkname = "outside"
    with pytest.raises(CmsEnablementError):
        extract_official_engine_archive(_archive({"root/link": link}), tmp_path)


def test_extract_official_engine_archive_rejects_unsupported_runner_dependencies(
    tmp_path: Path,
) -> None:
    archive = _archive(
        {
            "root/cms_migration_contract.json": json.dumps(
                {
                    "protocol_version": 1,
                    "runner_module": "build.migration.runner",
                    "required_runtime_packages": ["unreviewed-package"],
                }
            )
        }
    )

    with pytest.raises(CmsEnablementError):
        extract_official_engine_archive(archive, tmp_path)


def test_materialized_snapshot_includes_only_supported_text_and_inert_image_names(
    tmp_path: Path,
) -> None:
    async def scenario() -> None:
        ini = "[Comic Info]\nName = Example\n"
        client = _SnapshotClient(
            {
                SHA_A: GitHubGitBlob(
                    sha=SHA_A,
                    encoding="base64",
                    content=base64.b64encode(ini.encode()).decode(),
                )
            }
        )
        tree = GitHubGitTree(
            sha=SHA_B,
            tree=(
                GitHubGitTreeEntry(
                    path="your_content/comics",
                    mode="040000",
                    type="tree",
                    sha=SHA_C,
                ),
                _entry("your_content/comic_info.ini", SHA_A, len(ini.encode())),
                _entry("your_content/comics/page/image.jpg", SHA_C, 9),
                _entry("README.md", SHA_C, 9),
                _entry("your_content/comics/page/script.py", SHA_C, 9),
            ),
        )

        await materialize_target_snapshot(
            client,
            SecretStr("token"),
            _repository(),
            tree,
            tmp_path,
        )

        assert (tmp_path / "your_content/comic_info.ini").read_text(encoding="utf-8") == ini
        image = tmp_path / "your_content/comics/page/image.jpg"
        assert image.exists()
        assert image.stat().st_size == 0
        assert not (tmp_path / "README.md").exists()
        assert not (tmp_path / "your_content/comics/page/script.py").exists()

    asyncio.run(scenario())


def test_materialized_snapshot_rejects_windows_escape_and_links(tmp_path: Path) -> None:
    async def scenario() -> None:
        client = _SnapshotClient({})
        for entry in (
            _entry("your_content\\escape.ini", SHA_A, 1),
            GitHubGitTreeEntry(
                path="your_content/link.ini",
                mode="120000",
                type="blob",
                sha=SHA_A,
                size=1,
            ),
        ):
            with pytest.raises(CmsEnablementError):
                await materialize_target_snapshot(
                    client,
                    SecretStr("token"),
                    _repository(),
                    GitHubGitTree(sha=SHA_B, tree=(entry,)),
                    tmp_path,
                )

    asyncio.run(scenario())


def test_runner_result_allows_only_toml_changes_in_your_content() -> None:
    plan = parse_runner_plan(
        json.dumps(
            {
                "protocol_version": 1,
                "files": [{"path": "your_content/comic_info.toml", "content": "[cms]"}],
            }
        )
    )

    assert plan.files[0].path == "your_content/comic_info.toml"
    for invalid_path in ("README.md", "your_content/../escape.toml", "your_content\\escape.toml"):
        with pytest.raises(CmsEnablementError):
            parse_runner_plan(
                json.dumps(
                    {
                        "protocol_version": 1,
                        "files": [{"path": invalid_path, "content": "[cms]"}],
                    }
                )
            )


def test_engine_selector_reads_only_the_supported_main_config_forms() -> None:
    assert (
        parse_engine_selector(
            "your_content/comic_info.ini", "[Comic Settings]\nEngine version = cms"
        )
        == "cms"
    )
    assert (
        parse_engine_selector("your_content/comic_info.toml", "[engine]\nversion = '1.2'") == "1.2"
    )

    with pytest.raises(CmsEnablementError):
        parse_engine_selector("your_content/comic_info.toml", "[engine]\nversion = []")


def test_cms_enablement_errors_have_safe_troubleshooting_categories() -> None:
    error = CmsEnablementError("engine_contract_invalid")

    assert error.diagnostic_code == "engine_contract_invalid"
    assert "compatible CMS migration runner" in error.public_message


def test_decode_text_blob_accepts_github_style_multiline_base64() -> None:
    source = "[Comic Settings]\nEngine version = 1.1\n"
    blob = GitHubGitBlob(
        sha=SHA_A,
        encoding="base64",
        content=base64.encodebytes(source.encode()).decode(),
    )

    assert decode_text_blob(blob) == source


def test_target_state_reads_the_creator_selected_branch() -> None:
    class TargetStateClient:
        def __init__(self) -> None:
            self.requested_ref: str | None = None

        async def get_repository_ref(
            self,
            _token: SecretStr,
            _repository: GitHubRepository,
            ref: str,
        ) -> GitHubGitReference:
            self.requested_ref = ref
            return GitHubGitReference(
                ref="refs/heads/cms",
                object=GitHubGitObject(type="commit", sha=SHA_A),
            )

        async def get_repository_commit(
            self,
            _token: SecretStr,
            _repository: GitHubRepository,
            _sha: str,
        ) -> GitHubGitCommit:
            return GitHubGitCommit(sha=SHA_A, tree=GitHubGitSha(sha=SHA_B))

        async def get_repository_tree(
            self,
            _token: SecretStr,
            _repository: GitHubRepository,
            _sha: str,
        ) -> GitHubGitTree:
            return GitHubGitTree(
                sha=SHA_B,
                tree=(_entry("your_content/comic_info.ini", SHA_C, 38),),
            )

        async def get_repository_blob(
            self,
            _token: SecretStr,
            _repository: GitHubRepository,
            _sha: str,
        ) -> GitHubGitBlob:
            source = "[Comic Settings]\nEngine version = cms\n"
            return GitHubGitBlob(
                sha=SHA_C,
                encoding="base64",
                content=base64.b64encode(source.encode()).decode(),
            )

    async def scenario() -> None:
        client = TargetStateClient()
        state = await read_target_repository_state(client, SecretStr("token"), _repository(), "cms")

        assert client.requested_ref == "heads/cms"
        assert state.target_branch == "cms"
        assert state.engine_selector == "cms"

    asyncio.run(scenario())


def test_runner_uses_a_scrubbed_environment_and_fixed_module(tmp_path: Path) -> None:
    engine = MaterializedEngine(
        root=tmp_path / "engine",
        contract=EngineMigrationContract(1, "build.migration.runner", ("tomli-w",)),
    )
    runner_output = json.dumps(
        {
            "protocol_version": 1,
            "files": [{"path": "your_content/comic_info.toml", "content": "[cms]"}],
        }
    )
    with patch(
        "github_oauth_worker.cms_enablement.subprocess.run",
        return_value=subprocess.CompletedProcess([], 0, stdout=runner_output, stderr=""),
    ) as run:
        plan = run_engine_migration(engine, tmp_path / "snapshot", {"repository": "owner/comic"})

    assert plan.files[0].content == "[cms]"
    _, kwargs = run.call_args
    assert kwargs["env"] == {
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": str(engine.root / "src"),
        **({"SYSTEMROOT": kwargs["env"]["SYSTEMROOT"]} if "SYSTEMROOT" in kwargs["env"] else {}),
    }
    assert kwargs["cwd"] != engine.root.parent


def test_runner_failure_keeps_only_structured_safe_diagnostics(tmp_path: Path) -> None:
    engine = MaterializedEngine(
        root=tmp_path / "engine",
        contract=EngineMigrationContract(1, "build.migration.runner", ("tomli-w",)),
    )
    runner_failure = json.dumps({"protocol_version": 1, "failure_code": "internal_error"})

    with patch(
        "github_oauth_worker.cms_enablement.subprocess.run",
        return_value=subprocess.CompletedProcess([], 1, stdout="", stderr=runner_failure),
    ):
        with pytest.raises(CmsEnablementError) as raised:
            run_engine_migration(engine, tmp_path / "snapshot", {"repository": "owner/comic"})

    assert raised.value.diagnostic_code == "migration_runner_failed"
    assert raised.value.safe_log_fields() == {
        "runner_failure_kind": "nonzero_exit",
        "runner_returncode": 1,
        "runner_stdout_bytes": 0,
        "runner_stderr_bytes": len(runner_failure.encode("utf-8")),
        "runner_failure_code": "internal_error",
    }


def test_runner_failure_does_not_expose_unstructured_stderr(tmp_path: Path) -> None:
    engine = MaterializedEngine(
        root=tmp_path / "engine",
        contract=EngineMigrationContract(1, "build.migration.runner", ("tomli-w",)),
    )
    raw_stderr = "Traceback: target file contained secret-value"

    with patch(
        "github_oauth_worker.cms_enablement.subprocess.run",
        return_value=subprocess.CompletedProcess([], 1, stdout="", stderr=raw_stderr),
    ):
        with pytest.raises(CmsEnablementError) as raised:
            run_engine_migration(engine, tmp_path / "snapshot", {"repository": "owner/comic"})

    safe_fields = raised.value.safe_log_fields()
    assert safe_fields["runner_failure_code"] == "unclassified"
    assert "secret-value" not in repr(safe_fields)


def _archive(files: dict[str, str | tarfile.TarInfo]) -> bytes:
    result = io.BytesIO()
    with tarfile.open(fileobj=result, mode="w:gz") as archive:
        for path, value in files.items():
            if isinstance(value, tarfile.TarInfo):
                archive.addfile(value)
                continue
            contents = value.encode("utf-8")
            info = tarfile.TarInfo(path)
            info.size = len(contents)
            archive.addfile(info, io.BytesIO(contents))
    return result.getvalue()


def _entry(path: str, sha: str, size: int) -> GitHubGitTreeEntry:
    return GitHubGitTreeEntry(path=path, mode="100644", type="blob", sha=sha, size=size)


def _repository() -> GitHubRepository:
    return GitHubRepository(
        id=123,
        name="comic",
        owner=GitHubRepositoryOwner(login="owner"),
        default_branch="master",
    )


class _SnapshotClient:
    def __init__(self, blobs: dict[str, GitHubGitBlob]) -> None:
        self._blobs = blobs

    async def get_repository_blob(
        self,
        _token: SecretStr,
        _repository: GitHubRepository,
        sha: str,
    ) -> GitHubGitBlob:
        return self._blobs[sha]
