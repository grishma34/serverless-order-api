"""Assertions about the GitHub Actions workflows and the OIDC bootstrap.

The pipeline is the one part of this project that cannot be exercised locally —
there is no remote, and running it would touch a real AWS account. What *can* be
checked is that its security properties hold as written: no static credentials,
OIDC trust pinned to this repository, and no path that deploys untested code.
"""

from __future__ import annotations

import json
import pathlib
import re

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

    def _template(self) -> dict:
        with BOOTSTRAP_PATH.open() as handle:
            return yaml.load(handle, Loader=CloudFormationLoader)

    def _trusted_subject(self) -> str:
        """The subject the trust policy accepts, with parameter defaults filled in.

        Comparing the raw `!Sub` string would only ever prove the template says
        what the template says. Resolving it produces the literal claim value
        AWS will match against, which is what can be checked against the token
        the workflow will actually present.
        """
        subject = self._trust_conditions()["token.actions.githubusercontent.com:sub"]
        rendered = subject["Fn::Sub"] if isinstance(subject, dict) else subject
        for name, body in self._template()["Parameters"].items():
            # GitHubOrg has no default — it is supplied per account. Only the
            # shape of the subject matters there, so a sentinel stands in.
            rendered = rendered.replace(f"${{{name}}}", str(body.get("Default", f"<{name}>")))
        return rendered

    def test_subject_is_pinned_to_this_repository(self) -> None:
        subject = self._trust_conditions()["token.actions.githubusercontent.com:sub"]
        rendered = json.dumps(subject)
        assert "GitHubOrg" in rendered
        assert "GitHubRepo" in rendered

    def test_subject_carries_the_immutable_numeric_ids(self) -> None:
        """GitHub's default subject is `repo:ORG@ORGID/REPO@REPOID:...`.

        Dropping the ids yields a subject that looks right, reads right, and
        matches nothing — the second of the two failed production deploys here.
        Names alone would also be weaker: a deleted repository could be
        impersonated by a new one that later claims the same name.
        """
        subject = self._trusted_subject()
        expected = "repo:ORG@ORGID/REPO@REPOID:environment:NAME"
        assert re.fullmatch(r"repo:[^/@]+@[^/@]+/[^:@]+@[^:@]+:environment:.+", subject), (
            f"subject is not in GitHub's `{expected}` form: {subject}"
        )

    def test_every_placeholder_in_the_subject_resolves(self) -> None:
        # Guards the resolver above: an unresolved ${Placeholder} would sail
        # through the comparison below and prove nothing.
        assert "${" not in self._trusted_subject()

    def test_the_trust_subject_matches_what_deploy_presents(self) -> None:
        """The mismatch this catches cost a failed production deploy.

        GitHub swaps the OIDC token's `sub` claim depending on the job. A job
        that references an environment presents
        `repo:org/repo:environment:NAME`; only a job with no environment
        presents `repo:org/repo:ref:refs/heads/BRANCH`. This template pinned the
        branch form while `deploy.yml` declared `environment: production`, so
        STS refused every token the pipeline could ever mint.

        Neither file was wrong on its own, which is why every local check passed
        and the failure waited for a real deploy. The two are only comparable
        when read together — so they are read together here.
        """
        job = load_workflow("deploy.yml")["jobs"]["deploy"]
        environment = job.get("environment")
        if isinstance(environment, dict):
            environment = environment["name"]

        expected_tail = f"environment:{environment}" if environment else "ref:refs/heads/"
        assert expected_tail in self._trusted_subject(), (
            f"deploy.yml declares environment={environment!r}, so its OIDC token's "
            f"sub claim ends in {expected_tail!r} — which the trust policy "
            f"({self._trusted_subject()!r}) does not accept"
        )

    def test_the_role_the_pipeline_assumes_is_the_one_this_template_creates(self) -> None:
        # The other half of the same class of bug: a trust policy that matches
        # perfectly on a role the workflow never names.
        step = next(
            s
            for s in steps_of(load_workflow("deploy.yml"), "deploy")
            if "configure-aws-credentials" in s.get("uses", "")
        )
        assert "AWS_DEPLOY_ROLE_ARN" in step["with"]["role-to-assume"]
        assert "ApplicationStackPrefix" in json.dumps(self._role()["RoleName"])

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
