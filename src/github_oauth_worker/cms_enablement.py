"""Bounded engine-source, target-snapshot, and subprocess boundaries for CMS enablement."""

import base64
import configparser
import io
import json
import os
import subprocess
import sys
import tarfile
import tempfile
import tomllib
from dataclasses import dataclass
from pathlib import Path, PurePosixPath

from pydantic import SecretStr

from github_oauth_worker.engine_resolution import EngineSelector
from github_oauth_worker.errors import WorkerError
from github_oauth_worker.github_client import (
    GitHubAppClient,
    GitHubGitBlob,
    GitHubGitTree,
    GitHubRepository,
)

RUNNER_PROTOCOL_VERSION = 1
RUNNER_MODULE = "build.migration.runner"
MAXIMUM_ARCHIVE_BYTES = 10 * 1024 * 1024
MAXIMUM_ARCHIVE_FILES = 2_000
MAXIMUM_ARCHIVE_UNPACKED_BYTES = 25 * 1024 * 1024
MAXIMUM_ARCHIVE_PATH_DEPTH = 20
MAXIMUM_SNAPSHOT_FILES = 10_000
MAXIMUM_SNAPSHOT_TEXT_BYTES = 10 * 1024 * 1024
MAXIMUM_SNAPSHOT_TEXT_FILE_BYTES = 1 * 1024 * 1024
MAXIMUM_RUNNER_OUTPUT_BYTES = 10 * 1024 * 1024
RUNNER_TIMEOUT_SECONDS = 30
TEXT_SOURCE_SUFFIXES = frozenset({".ini", ".toml", ".txt", ".md", ".json"})
IMAGE_SUFFIXES = frozenset(
    {".jpg", ".jpeg", ".png", ".tif", ".tiff", ".gif", ".bmp", ".webp", ".webv", ".svg", ".eps"}
)
SUPPORTED_RUNNER_RUNTIME_PACKAGES = frozenset({"tomli-w"})


class CmsEnablementError(WorkerError):
    """Reject an unsafe or unsupported CMS enablement operation without exposing internals."""

    _PUBLIC_MESSAGES = {
        "repository_config_invalid": (
            "CMS setup requires exactly one readable `your_content/comic_info.ini` or "
            "`your_content/comic_info.toml` file with an engine version."
        ),
        "repository_revision_invalid": (
            "CMS setup could not read the selected repository branch revision. Try again after "
            "confirming the branch still exists and contains an ordinary commit."
        ),
        "target_content_invalid": (
            "CMS setup could not safely read this repository's `your_content` files. Use ordinary "
            "files with supported UTF-8 source text, then try again."
        ),
        "engine_source_invalid": (
            "The configured comic_git engine source could not be loaded safely. Confirm that the "
            "configured engine version exists and is supported by this worker."
        ),
        "engine_contract_invalid": (
            "The configured comic_git engine version does not include a compatible CMS migration "
            "runner. Update to a compatible engine version and try again."
        ),
        "migration_runner_failed": (
            "The configured comic_git engine could not prepare a CMS migration. Update to a "
            "compatible engine version and try again."
        ),
        "migration_plan_invalid": (
            "The configured comic_git engine returned an invalid CMS migration plan. Update to a "
            "compatible engine version and try again."
        ),
    }
    diagnostic_code = "cms_enablement_failed"
    public_message = "This repository cannot be prepared for CMS enablement by this worker."

    def __init__(self, diagnostic_code: str = "cms_enablement_failed") -> None:
        """Map an internal boundary category to a safe troubleshooting response."""
        self.diagnostic_code = diagnostic_code
        self.public_message = self._PUBLIC_MESSAGES.get(diagnostic_code, type(self).public_message)


@dataclass(frozen=True)
class EngineMigrationContract:
    """The fixed executable contract declared by a trusted official engine source archive."""

    protocol_version: int
    runner_module: str
    required_runtime_packages: tuple[str, ...]


@dataclass(frozen=True)
class MaterializedEngine:
    """An extracted official engine source root and its validated migration contract."""

    root: Path
    contract: EngineMigrationContract


@dataclass(frozen=True)
class MigrationPlanFile:
    """One generated UTF-8 file that may be included in a migration commit."""

    path: str
    content: str


@dataclass(frozen=True)
class RunnerMigrationPlan:
    """A validated migration-runner response that contains no executable target content."""

    files: tuple[MigrationPlanFile, ...]


@dataclass(frozen=True)
class TargetRepositoryState:
    """Verified target revision data needed to plan a migration without a repository checkout."""

    base_commit_sha: str
    target_branch: str
    tree: GitHubGitTree
    config_path: str
    engine_selector: str


@dataclass(frozen=True)
class CmsMigrationPreview:
    """Non-secret creator-facing migration summary produced by a fixed engine revision."""

    base_commit_sha: str
    target_branch: str
    engine_selector: str
    engine_commit_sha: str
    changed_paths: tuple[str, ...]
    files: tuple[MigrationPlanFile, ...]


def extract_official_engine_archive(archive: bytes, destination: Path) -> MaterializedEngine:
    """Extract a bounded official source archive without trusting archive paths or links."""
    if len(archive) > MAXIMUM_ARCHIVE_BYTES:
        raise CmsEnablementError("engine_source_invalid")
    try:
        with tarfile.open(fileobj=io.BytesIO(archive), mode="r:gz") as source:
            members = source.getmembers()
            if len(members) > MAXIMUM_ARCHIVE_FILES:
                raise CmsEnablementError("engine_source_invalid")
            root_name = validate_archive_members(members)
            extracted_root = destination / root_name
            total_size = 0
            for member in members:
                if member.isdir():
                    continue
                total_size += member.size
                if total_size > MAXIMUM_ARCHIVE_UNPACKED_BYTES:
                    raise CmsEnablementError("engine_source_invalid")
                target = destination.joinpath(*PurePosixPath(member.name).parts)
                target.parent.mkdir(parents=True, exist_ok=True)
                extracted_file = source.extractfile(member)
                if extracted_file is None:
                    raise CmsEnablementError("engine_source_invalid")
                target.write_bytes(extracted_file.read())
    except OSError, tarfile.TarError:
        raise CmsEnablementError("engine_source_invalid") from None
    return MaterializedEngine(extracted_root, load_engine_contract(extracted_root))


def validate_archive_members(members: list[tarfile.TarInfo]) -> str:
    """Return one archive root after rejecting links, special files, and path escapes."""
    root_names: set[str] = set()
    for member in members:
        path = parse_safe_posix_path(member.name)
        if path is None:
            raise CmsEnablementError("engine_source_invalid")
        root_names.add(path.parts[0])
        if not (member.isdir() or member.isfile()):
            raise CmsEnablementError("engine_source_invalid")
    if len(root_names) != 1:
        raise CmsEnablementError("engine_source_invalid")
    return root_names.pop()


def load_engine_contract(engine_root: Path) -> EngineMigrationContract:
    """Load the engine-declared protocol after archive extraction has established its root."""
    try:
        with open(engine_root / "cms_migration_contract.json", encoding="utf-8") as f:
            data = json.load(f)
    except OSError, ValueError:
        raise CmsEnablementError("engine_contract_invalid") from None
    if not isinstance(data, dict) or data.get("protocol_version") != RUNNER_PROTOCOL_VERSION:
        raise CmsEnablementError("engine_contract_invalid")
    if data.get("runner_module") != RUNNER_MODULE:
        raise CmsEnablementError("engine_contract_invalid")
    packages = data.get("required_runtime_packages")
    if (
        not isinstance(packages, list)
        or not all(isinstance(package, str) for package in packages)
        or not set(packages).issubset(SUPPORTED_RUNNER_RUNTIME_PACKAGES)
    ):
        raise CmsEnablementError("engine_contract_invalid")
    return EngineMigrationContract(
        protocol_version=data["protocol_version"],
        runner_module=data["runner_module"],
        required_runtime_packages=tuple(packages),
    )


async def materialize_target_snapshot(
    client: GitHubAppClient,
    user_access_token: SecretStr,
    repository: GitHubRepository,
    tree: GitHubGitTree,
    destination: Path,
) -> None:
    """Write only migration input text and inert image names from a validated Git tree."""
    if tree.truncated or len(tree.tree) > MAXIMUM_SNAPSHOT_FILES:
        raise CmsEnablementError("target_content_invalid")
    total_text_bytes = 0
    for entry in tree.tree:
        path = validated_content_path(entry.path)
        if path is None:
            continue
        if entry.type == "tree":
            continue
        if entry.type != "blob" or entry.mode == "120000":
            raise CmsEnablementError("target_content_invalid")
        target = destination.joinpath(*PurePosixPath(path).parts)
        suffix = target.suffix.casefold()
        if suffix in IMAGE_SUFFIXES:
            target.parent.mkdir(parents=True, exist_ok=True)
            target.touch(exist_ok=False)
            continue
        if suffix not in TEXT_SOURCE_SUFFIXES:
            continue
        if entry.size is None or entry.size > MAXIMUM_SNAPSHOT_TEXT_FILE_BYTES:
            raise CmsEnablementError("target_content_invalid")
        total_text_bytes += entry.size
        if total_text_bytes > MAXIMUM_SNAPSHOT_TEXT_BYTES:
            raise CmsEnablementError("target_content_invalid")
        blob = await client.get_repository_blob(user_access_token, repository, entry.sha)
        text = decode_text_blob(blob)
        if len(text.encode("utf-8")) != entry.size:
            raise CmsEnablementError("target_content_invalid")
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(text, encoding="utf-8", newline="\n")


def validated_content_path(value: str) -> str | None:
    """Accept only ordinary paths within your_content and reject traversal-like Git tree names."""
    path = parse_safe_posix_path(value)
    if path is None:
        raise CmsEnablementError("target_content_invalid")
    if path.parts[0] != "your_content":
        return None
    return path.as_posix()


def parse_safe_posix_path(value: str) -> PurePosixPath | None:
    """Parse a bounded portable relative path before materializing it on the worker filesystem."""
    if "\\" in value or "\x00" in value:
        return None
    path = PurePosixPath(value)
    if (
        path.is_absolute()
        or not path.parts
        or len(path.parts) > MAXIMUM_ARCHIVE_PATH_DEPTH
        or any(part in {"", ".", ".."} or ":" in part for part in path.parts)
    ):
        return None
    return path


def decode_text_blob(blob: GitHubGitBlob) -> str:
    """Decode GitHub's standard multiline Base64 blob form as UTF-8 source text."""
    if blob.encoding != "base64":
        raise CmsEnablementError("target_content_invalid")
    try:
        return base64.b64decode(blob.content).decode("utf-8")
    except UnicodeDecodeError, ValueError:
        raise CmsEnablementError("target_content_invalid") from None


def run_engine_migration(
    engine: MaterializedEngine,
    snapshot_root: Path,
    cms_enablement: dict[str, object],
) -> RunnerMigrationPlan:
    """Run the fixed engine module with a scrubbed environment and validate its JSON output."""
    request = json.dumps(
        {
            "repository_root": str(snapshot_root),
            "cms_enablement": cms_enablement,
        }
    )
    environment = {
        "PYTHONIOENCODING": "utf-8",
        "PYTHONPATH": str(engine.root / "src"),
    }
    if os.name == "nt" and "SYSTEMROOT" in os.environ:
        environment["SYSTEMROOT"] = os.environ["SYSTEMROOT"]
    try:
        with tempfile.TemporaryDirectory() as working_directory:
            result = subprocess.run(
                [sys.executable, "-m", engine.contract.runner_module],
                input=request,
                text=True,
                encoding="utf-8",
                capture_output=True,
                cwd=working_directory,
                env=environment,
                timeout=RUNNER_TIMEOUT_SECONDS,
                check=False,
            )
    except OSError, subprocess.TimeoutExpired:
        raise CmsEnablementError("migration_runner_failed") from None
    if result.returncode != 0 or len(result.stdout.encode("utf-8")) > MAXIMUM_RUNNER_OUTPUT_BYTES:
        raise CmsEnablementError("migration_runner_failed")
    return parse_runner_plan(result.stdout)


def parse_runner_plan(output: str) -> RunnerMigrationPlan:
    """Validate that a runner response can alter only normalized TOML paths under your_content."""
    try:
        data = json.loads(output)
    except ValueError:
        raise CmsEnablementError("migration_plan_invalid") from None
    if not isinstance(data, dict) or data.get("protocol_version") != RUNNER_PROTOCOL_VERSION:
        raise CmsEnablementError("migration_plan_invalid")
    files = data.get("files")
    if not isinstance(files, list) or not files:
        raise CmsEnablementError("migration_plan_invalid")
    planned_files = []
    paths = set()
    for file_data in files:
        if not isinstance(file_data, dict):
            raise CmsEnablementError("migration_plan_invalid")
        path = file_data.get("path")
        content = file_data.get("content")
        if not isinstance(path, str) or not isinstance(content, str):
            raise CmsEnablementError("migration_plan_invalid")
        if path in paths or validated_content_path(path) != path or not path.endswith(".toml"):
            raise CmsEnablementError("migration_plan_invalid")
        paths.add(path)
        planned_files.append(MigrationPlanFile(path, content))
    return RunnerMigrationPlan(tuple(planned_files))


async def read_target_repository_state(
    client: GitHubAppClient,
    user_access_token: SecretStr,
    repository: GitHubRepository,
    target_branch: str,
) -> TargetRepositoryState:
    """Read one selected branch revision and its main-config selector before source resolution."""
    if not target_branch:
        raise CmsEnablementError("repository_revision_invalid")
    ref = await client.get_repository_ref(user_access_token, repository, f"heads/{target_branch}")
    if ref.object.type != "commit":
        raise CmsEnablementError("repository_revision_invalid")
    commit = await client.get_repository_commit(user_access_token, repository, ref.object.sha)
    tree = await client.get_repository_tree(user_access_token, repository, commit.tree.sha)
    if tree.truncated:
        raise CmsEnablementError("repository_revision_invalid")
    config_entry = _main_config_entry(tree)
    blob = await client.get_repository_blob(user_access_token, repository, config_entry.sha)
    return TargetRepositoryState(
        base_commit_sha=commit.sha,
        target_branch=target_branch,
        tree=tree,
        config_path=config_entry.path,
        engine_selector=parse_engine_selector(config_entry.path, decode_text_blob(blob)),
    )


async def build_migration_preview(
    client: GitHubAppClient,
    user_access_token: SecretStr,
    repository: GitHubRepository,
    state: TargetRepositoryState,
    selector: EngineSelector,
    engine_commit_sha: str,
    cms_enablement: dict[str, object],
) -> CmsMigrationPreview:
    """Run an already-resolved official engine revision and return its migration summary."""
    archive = await client.download_official_engine_archive(engine_commit_sha)
    with tempfile.TemporaryDirectory() as workspace:
        workspace_root = Path(workspace)
        engine = extract_official_engine_archive(archive, workspace_root / "engine")
        snapshot_root = workspace_root / "snapshot"
        await materialize_target_snapshot(
            client,
            user_access_token,
            repository,
            state.tree,
            snapshot_root,
        )
        plan = run_engine_migration(engine, snapshot_root, cms_enablement)
    return CmsMigrationPreview(
        base_commit_sha=state.base_commit_sha,
        target_branch=state.target_branch,
        engine_selector=selector.value,
        engine_commit_sha=engine_commit_sha,
        changed_paths=tuple(file.path for file in plan.files),
        files=plan.files,
    )


async def resolve_official_engine_commit(
    client: GitHubAppClient,
    selector: EngineSelector,
) -> str:
    """Resolve one approved official branch to a verified immutable commit SHA."""
    reference = await client.resolve_official_engine_ref(selector.ref)
    if reference.object.type != "commit":
        raise CmsEnablementError("engine_source_invalid")
    commit = await client.get_official_engine_commit(reference.object.sha)
    return commit.sha


def parse_engine_selector(config_path: str, contents: str) -> str:
    """Read only the main config's engine version in the two supported config formats."""
    try:
        if config_path.endswith(".toml"):
            data = tomllib.loads(contents)
            engine = data.get("engine")
            value = engine.get("version") if isinstance(engine, dict) else None
        else:
            parser = configparser.ConfigParser(interpolation=None)
            parser.read_string(contents)
            value = parser.get("Comic Settings", "Engine version", fallback=None)
    except configparser.Error, tomllib.TOMLDecodeError:
        raise CmsEnablementError("repository_config_invalid") from None
    if not isinstance(value, str) or not value.strip():
        raise CmsEnablementError("repository_config_invalid")
    return value.strip()


def _main_config_entry(tree: GitHubGitTree):
    """Select exactly one supported main config and reject ambiguous CMS migration input."""
    entries = [
        entry
        for entry in tree.tree
        if entry.path in {"your_content/comic_info.toml", "your_content/comic_info.ini"}
    ]
    if len(entries) != 1 or entries[0].type != "blob" or entries[0].mode == "120000":
        raise CmsEnablementError("repository_config_invalid")
    return entries[0]
