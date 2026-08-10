"""Assertions about the GitHub Actions workflows and the OIDC bootstrap.

The pipeline is the one part of this project that cannot be exercised locally —
there is no remote, and running it would touch a real AWS account. What *can* be
checked is that its security properties hold as written: no static credentials,
OIDC trust pinned to this repository, and no path that deploys untested code.
"""

from __future__ import annotations

import json
import pathlib

import pytest
import yaml

from tests.template import CloudFormationLoader

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
WORKFLOW_DIR = REPO_ROOT / ".github" / "workflows"
BOOTSTRAP_PATH = REPO_ROOT / "bootstrap" / "github-oidc.yaml"

# Anything that would mean a long-lived AWS credential lives in the repo,
# defeating REQ-0023.
STATIC_CREDENTIAL_MARKERS = (
    "aws-access-key-id",
    "aws-secret-access-key",
    "AWS_ACCESS_KEY_ID",
    "AWS_SECRET_ACCESS_KEY",
    "AWS_SESSION_TOKEN",
)


def workflow_files() -> list[pathlib.Path]:
    return sorted(WORKFLOW_DIR.glob("*.yml")) + sorted(WORKFLOW_DIR.glob("*.yaml"))


def load_workflow(name: str) -> dict:
    with (WORKFLOW_DIR / name).open() as handle:
        return yaml.safe_load(handle)


def steps_of(workflow: dict, job: str) -> list[dict]:
    return workflow["jobs"][job]["steps"]


def run_script(workflow: dict, job: str) -> str:
    return "\n".join(step.get("run", "") for step in steps_of(workflow, job))


# `on:` is parsed by PyYAML 1.1 rules as the boolean True, not the string "on".
TRIGGER_KEY = True


class TestNoStaticCredentials:
    """REQ-0023: no long-lived AWS keys in GitHub secrets."""

    def test_at_least_one_workflow_exists(self) -> None:
        # Guards the guard: an empty glob makes every check below vacuous.
        assert len(workflow_files()) >= 2

    @pytest.mark.parametrize("path", workflow_files(), ids=lambda p: p.name)
    def test_no_workflow_reads_an_access_key(self, path: pathlib.Path) -> None:
        contents = path.read_text()
        for marker in STATIC_CREDENTIAL_MARKERS:
            assert marker not in contents, f"{path.name} references {marker}"

    def test_deploy_assumes_a_role_instead(self) -> None:
        deploy = load_workflow("deploy.yml")
        credential_step = next(
            step
            for step in steps_of(deploy, "deploy")
            if "configure-aws-credentials" in step.get("uses", "")
        )
        assert "role-to-assume" in credential_step["with"]

    def test_deploy_requests_an_oidc_token(self) -> None:
        # Without id-token: write the runner cannot mint the OIDC token and role
        # assumption fails outright.
        assert load_workflow("deploy.yml")["permissions"]["id-token"] == "write"

    def test_deploy_does_not_take_write_access_to_the_repo(self) -> None:
        assert load_workflow("deploy.yml")["permissions"]["contents"] == "read"


class TestDeployCannotOutrunTheGate:
    def test_deploy_job_depends_on_tests(self) -> None:
        assert load_workflow("deploy.yml")["jobs"]["deploy"]["needs"] == "test"

    def test_the_test_job_enforces_the_coverage_gate(self) -> None:
        # A deploy job that depends on a test job which does not gate coverage
        # would satisfy the dependency while proving nothing.
        assert "--cov-fail-under=90" in run_script(load_workflow("deploy.yml"), "test")

    def test_the_test_job_lints(self) -> None:
        script = run_script(load_workflow("deploy.yml"), "test")
        assert "ruff check" in script

    def test_deploy_only_triggers_on_main(self) -> None:
        triggers = load_workflow("deploy.yml")[TRIGGER_KEY]
        assert triggers["push"]["branches"] == ["main"]

    def test_deploy_is_not_triggered_by_pull_requests(self) -> None:
        # A pull_request trigger would let a fork's PR deploy to production.
        assert "pull_request" not in load_workflow("deploy.yml")[TRIGGER_KEY]

    def test_deploys_are_serialised_and_never_cancelled(self) -> None:
        # Cancelling mid-update strands the stack in UPDATE_IN_PROGRESS.
        concurrency = load_workflow("deploy.yml")["concurrency"]
        assert concurrency["cancel-in-progress"] is False


class TestDeploySteps:
    def test_deploy_is_non_interactive(self) -> None:
        script = run_script(load_workflow("deploy.yml"), "deploy")
        assert "--no-confirm-changeset" in script

    def test_an_empty_changeset_is_not_a_failure(self) -> None:
        # A docs-only merge produces no stack diff; that must not fail the run.
        assert "--no-fail-on-empty-changeset" in run_script(load_workflow("deploy.yml"), "deploy")

    def test_frontend_is_synced_and_the_cache_invalidated(self) -> None:
        script = run_script(load_workflow("deploy.yml"), "deploy")
        assert "s3 sync" in script
        assert "create-invalidation" in script

    def test_sync_deletes_removed_files(self) -> None:
        # Without --delete a file deleted from the repo lingers on the site.
        assert "--delete" in run_script(load_workflow("deploy.yml"), "deploy")

    def test_build_runs_before_deploy(self) -> None:
        script = run_script(load_workflow("deploy.yml"), "deploy")
        assert script.index("sam build") < script.index("sam deploy")


class TestCiWorkflow:
    def test_ci_runs_on_pull_requests(self) -> None:
        # REQ-0024 depends on this: branch protection gates on these checks.
        assert "pull_request" in load_workflow("ci.yml")[TRIGGER_KEY]

    def test_ci_job_names_match_the_documented_protection_contexts(self) -> None:
        # docs/DEPLOYMENT.md pins these as required status checks; renaming a
        # job without updating that would silently disable branch protection.
        jobs = set(load_workflow("ci.yml")["jobs"])
        documented = (REPO_ROOT / "docs" / "DEPLOYMENT.md").read_text()

        assert jobs == {"quality-gate", "template"}
        for job in jobs:
            assert f'"{job}"' in documented

    def test_ci_validates_the_template(self) -> None:
        assert "sam validate --lint" in run_script(load_workflow("ci.yml"), "template")


class TestOidcBootstrap:
    """The trust policy is the whole security boundary — check it precisely."""

    def _role(self) -> dict:
        with BOOTSTRAP_PATH.open() as handle:
            template = yaml.load(handle, Loader=CloudFormationLoader)
        return template["Resources"]["DeployRole"]["Properties"]

    def _trust_conditions(self) -> dict:
        (statement,) = self._role()["AssumeRolePolicyDocument"]["Statement"]
        return statement["Condition"]["StringEquals"]

    def test_trust_is_web_identity_only(self) -> None:
        (statement,) = self._role()["AssumeRolePolicyDocument"]["Statement"]
        assert statement["Action"] == "sts:AssumeRoleWithWebIdentity"
        assert "Federated" in statement["Principal"]

    def test_audience_is_pinned(self) -> None:
        # Without aud, a token minted for another audience could be replayed.
        conditions = self._trust_conditions()
        assert conditions["token.actions.githubusercontent.com:aud"] == "sts.amazonaws.com"

    def test_subject_is_pinned_to_a_repo_and_branch(self) -> None:
        conditions = self._trust_conditions()
        subject = conditions["token.actions.githubusercontent.com:sub"]

        rendered = json.dumps(subject)
        assert "GitHubOrg" in rendered
        assert "GitHubRepo" in rendered
        assert "refs/heads/" in rendered

    def test_subject_uses_string_equals_not_a_wildcard(self) -> None:
        """StringLike with `repo:org/*` would let any repo in the org deploy."""
        (statement,) = self._role()["AssumeRolePolicyDocument"]["Statement"]
        condition = statement["Condition"]

        assert set(condition) == {"StringEquals"}
        assert "*" not in json.dumps(condition["StringEquals"])

    def test_role_cannot_modify_itself(self) -> None:
        # iam:* scoped to the stack prefix, so the pipeline cannot widen its own
        # permissions or rewrite its own trust policy.
        (policy,) = self._role()["Policies"]
        iam_statement = next(
            s for s in policy["PolicyDocument"]["Statement"] if s["Sid"] == "ManageFunctionRoles"
        )
        assert iam_statement["Resource"] != "*"
        assert "role/" in json.dumps(iam_statement["Resource"])

    def test_no_statement_grants_full_admin(self) -> None:
        (policy,) = self._role()["Policies"]
        for statement in policy["PolicyDocument"]["Statement"]:
            actions = statement["Action"]
            actions = [actions] if isinstance(actions, str) else actions
            assert "*" not in actions, f"{statement['Sid']} grants blanket admin"

    def test_bucket_access_is_scoped_by_name(self) -> None:
        (policy,) = self._role()["Policies"]
        buckets = next(
            s for s in policy["PolicyDocument"]["Statement"] if s["Sid"] == "ManageBuckets"
        )
        assert buckets["Resource"] != "*"

    def test_provider_creation_is_optional(self) -> None:
        # An account may hold only one GitHub OIDC provider; a second create
        # fails the whole stack.
        with BOOTSTRAP_PATH.open() as handle:
            template = yaml.load(handle, Loader=CloudFormationLoader)

        assert "CreateOIDCProvider" in template["Parameters"]
        assert template["Resources"]["GitHubOIDCProvider"]["Condition"]

    def test_role_arn_is_exposed_for_the_repo_variable(self) -> None:
        with BOOTSTRAP_PATH.open() as handle:
            template = yaml.load(handle, Loader=CloudFormationLoader)

        assert "DeployRoleArn" in template["Outputs"]
