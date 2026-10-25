"""Guard the approval and credential boundaries of the GitHub Actions workflow."""

from pathlib import Path

import yaml

WORKFLOW = Path(__file__).resolve().parents[2] / ".github" / "workflows" / "ci.yml"


def test_pulumi_applies_require_preview_and_protected_environment() -> None:
    workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    jobs = workflow["jobs"]

    assert set(jobs) == {
        "test",
        "preview-test",
        "preview-production",
        "apply-test",
        "apply-production",
    }
    assert set(workflow["on"]) == {"push", "pull_request"}
    assert jobs["preview-test"]["environment"] == "test-preview"
    assert jobs["preview-production"]["environment"] == "production-preview"
    assert jobs["apply-test"]["environment"] == "test"
    assert jobs["apply-production"]["environment"] == "production"
    assert jobs["apply-test"]["needs"] == "preview-test"
    assert set(jobs["apply-production"]["needs"]) == {"preview-production", "apply-test"}

    for job_name, job in jobs.items():
        commands = "\n".join(step.get("run", "") for step in job["steps"])
        if job_name.startswith("apply-"):
            assert "pulumi preview" in commands
            assert "pulumi up" in commands
            assert "git ls-remote" in commands
        else:
            assert "pulumi up" not in commands
        if job_name != "test":
            assert job["permissions"]["id-token"] == "write"
        else:
            assert "id-token" not in job.get("permissions", {})

    assert "github.event_name == 'push'" in jobs["apply-test"]["if"]
    assert "refs/heads/master" in jobs["apply-production"]["if"]
    assert "github.event.pull_request.head.repo.full_name == github.repository" in jobs[
        "preview-test"
    ]["if"]


def test_external_actions_are_pinned_to_commits() -> None:
    workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    for job in workflow["jobs"].values():
        for step in job["steps"]:
            action = step.get("uses")
            if action is None:
                continue
            assert "@" in action
            revision = action.rsplit("@", 1)[1]
            assert len(revision) == 40
            assert all(char in "0123456789abcdef" for char in revision)


def test_pulumi_commands_have_separate_named_steps() -> None:
    workflow = yaml.load(WORKFLOW.read_text(encoding="utf-8"), Loader=yaml.BaseLoader)
    for environment in ("test", "production"):
        for job_name in (f"preview-{environment}", f"apply-{environment}"):
            steps = workflow["jobs"][job_name]["steps"]
            named_steps = {step["name"]: step for step in steps if "name" in step}
            expected_names = [
                "Install Pulumi dependencies",
                "Log in to Pulumi backend",
                f"Preview {environment} infrastructure",
            ]
            if job_name.startswith("apply-"):
                expected_names.append(f"Apply {environment} infrastructure")
            positions = [
                next(index for index, step in enumerate(steps) if step.get("name") == name)
                for name in expected_names
            ]
            assert positions == sorted(positions)
            for name in expected_names:
                assert named_steps[name]["working-directory"] == "infra"
            assert "python -m venv .venv" in named_steps[expected_names[0]]["run"]
            assert "pip install -r requirements.txt" in named_steps[expected_names[0]]["run"]
            assert "pulumi " not in named_steps[expected_names[0]]["run"]
            assert named_steps[expected_names[1]]["run"] == 'pulumi login "$PULUMI_BACKEND_URL"'
            assert named_steps[expected_names[2]]["run"] == (
                f"pulumi preview --stack {environment} --diff --non-interactive"
            )
            if job_name.startswith("apply-"):
                assert named_steps[expected_names[3]]["run"] == (
                    f"pulumi up --stack {environment} --yes --non-interactive"
                )
