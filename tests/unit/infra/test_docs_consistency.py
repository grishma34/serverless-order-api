"""Keep the documented claims true.

A README is the part of a project most likely to drift, because nothing executes
it. These tests pin the handful of numbers and identifiers it asserts to the
things that actually produce them, so a change to the design shows up as a
failing test rather than as a stale sentence someone reads in an interview.

Deliberately narrow: prose is not checked, only claims with a machine-checkable
counterpart.
"""

from __future__ import annotations

import pathlib
import re

import pytest

from services.status_machine import TRANSITIONS
from shared.models import OrderStatus
from tests.template import load_template, resources_of_type

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]

README = REPO_ROOT / "readme.md"
TASKS = REPO_ROOT / "tasks.md"
PLAN = REPO_ROOT / "PLAN.md"
AGENT_RULES = REPO_ROOT / "claude.md"
DOCS = REPO_ROOT / "docs"

REQUIREMENTS = DOCS / "REQUIREMENTS.md"
DYNAMODB_DESIGN = DOCS / "DYNAMODB_DESIGN.md"
API_SPEC = DOCS / "API_SPEC.md"
EVIDENCE = DOCS / "EVIDENCE.md"
SMOKE_EVIDENCE = DOCS / "SMOKE_EVIDENCE.md"
DEPLOYMENT = DOCS / "DEPLOYMENT.md"

REQUIREMENT_ID = re.compile(r"\b(?:REQ|NFR)-\d{4}\b")

# Documents that cite requirement IDs and must only cite real ones.
CITING_DOCS = [README, TASKS, PLAN, AGENT_RULES, DYNAMODB_DESIGN, API_SPEC, DEPLOYMENT]


def known_requirement_ids() -> set[str]:
    return set(REQUIREMENT_ID.findall(REQUIREMENTS.read_text()))


class TestRequirementIdsResolve:
    def test_the_register_is_not_empty(self) -> None:
        # Guards the guard.
        assert len(known_requirement_ids()) >= 20

    @pytest.mark.parametrize("path", CITING_DOCS, ids=lambda p: p.name)
    def test_every_cited_id_exists(self, path: pathlib.Path) -> None:
        cited = set(REQUIREMENT_ID.findall(path.read_text()))
        unknown = cited - known_requirement_ids()
        assert not unknown, f"{path.name} cites ids absent from REQUIREMENTS.md: {sorted(unknown)}"

    def test_source_code_only_cites_real_ids(self) -> None:
        unknown: set[str] = set()
        for path in (REPO_ROOT / "src").rglob("*.py"):
            unknown |= set(REQUIREMENT_ID.findall(path.read_text())) - known_requirement_ids()
        assert not unknown, f"src/ cites unknown requirement ids: {sorted(unknown)}"


class TestAccessPatternCount:
    def test_the_design_documents_six(self) -> None:
        # The README's headline "6 access patterns" comes from this table.
        rows = re.findall(r"^\| AP\d ", DYNAMODB_DESIGN.read_text(), re.MULTILINE)
        assert len(rows) == 6

    def test_the_readme_agrees(self) -> None:
        rows = re.findall(r"^\| AP\d ", DYNAMODB_DESIGN.read_text(), re.MULTILINE)
        assert f"{len(rows)} documented access patterns" in README.read_text()

    def test_every_access_pattern_is_numbered_uniquely(self) -> None:
        ids = re.findall(r"^\| (AP\d) ", DYNAMODB_DESIGN.read_text(), re.MULTILINE)
        assert len(ids) == len(set(ids))


class TestCoverageClaims:
    def _threshold(self, text: str) -> set[str]:
        return set(re.findall(r"--cov-fail-under=(\d+)", text))

    def test_ci_and_readme_state_the_same_threshold(self) -> None:
        ci = (REPO_ROOT / ".github" / "workflows" / "ci.yml").read_text()
        assert self._threshold(ci) == self._threshold(README.read_text()) == {"90"}

    def test_the_deploy_pipeline_uses_the_same_threshold(self) -> None:
        deploy = (REPO_ROOT / ".github" / "workflows" / "deploy.yml").read_text()
        assert self._threshold(deploy) == {"90"}

    def test_the_requirement_register_states_the_same_threshold(self) -> None:
        assert "90" in re.search(r"NFR-0001.*", REQUIREMENTS.read_text()).group(0)

    def test_the_badge_reports_at_least_the_gate(self) -> None:
        # A badge claiming less than the enforced gate would mean the badge is
        # stale, since the suite cannot pass below it.
        badge = (DOCS / "assets" / "coverage.svg").read_text()
        percent = int(re.search(r"coverage: (\d+)%", badge).group(1))
        assert percent >= 90


class TestEndpointsMatchTheTemplate:
    def _template_routes(self) -> set[tuple[str, str]]:
        routes = set()
        for body in resources_of_type("AWS::Serverless::Function").values():
            (event,) = body["Properties"]["Events"].values()
            routes.add((event["Properties"]["Method"].upper(), event["Properties"]["Path"]))
        return routes

    def test_readme_lists_every_deployed_route(self) -> None:
        readme = README.read_text()
        for method, path in self._template_routes():
            # The README writes the ops listing with its query string.
            documented = path in readme or f"{path}?status=X" in readme
            assert documented, f"{method} {path} is deployed but not in the README"

    def test_readme_documents_no_route_that_does_not_exist(self) -> None:
        deployed = {path for _, path in self._template_routes()}
        cited = set(re.findall(r"`(/api/[^`?]+)`", README.read_text()))
        # `/api` alone is the base path and `/api/*` is the CloudFront behaviour
        # pattern; neither is a route.
        cited.discard("/api")
        cited = {path for path in cited if "*" not in path}
        assert cited <= deployed, f"README documents undeployed routes: {sorted(cited - deployed)}"


class TestStateMachineDocumentation:
    def test_the_readme_diagram_names_every_status(self) -> None:
        readme = README.read_text()
        for status in OrderStatus:
            assert status.value in readme

    def test_the_api_spec_names_every_status(self) -> None:
        spec = API_SPEC.read_text()
        for status in OrderStatus:
            assert status.value in spec

    def test_terminal_states_are_described_as_terminal(self) -> None:
        readme = README.read_text()
        terminal = [s.value for s in OrderStatus if not TRANSITIONS[s]]
        assert set(terminal) == {"DELIVERED", "CANCELLED"}
        assert "terminal" in readme


class TestRuntimeClaims:
    def test_readme_states_the_deployed_runtime(self) -> None:
        runtime = load_template()["Globals"]["Function"]["Runtime"]
        assert runtime in README.read_text()

    def test_python_version_file_matches_the_lambda_runtime(self) -> None:
        # A local venv on a different minor version would test code that cannot
        # run on the deployed runtime.
        pinned = (REPO_ROOT / ".python-version").read_text().strip()
        runtime = load_template()["Globals"]["Function"]["Runtime"]
        assert runtime == f"python{pinned}"

    def test_architecture_claim_matches_the_template(self) -> None:
        architectures = load_template()["Globals"]["Function"]["Architectures"]
        assert architectures == ["arm64"]
        assert "arm64" in README.read_text()


class TestHonestyAboutDeployment:
    """The README makes a strong claim about what *has* happened.

    It used to claim the opposite, and these tests were the tripwire for
    deploying and forgetting to update it. They now guard the other direction:
    the advertised URL has to be the one that was actually smoke-tested, and the
    smoke run behind it has to have passed.
    """

    def _advertised_url(self, text: str) -> str | None:
        match = re.search(r"https://[a-z0-9]+\.cloudfront\.net", text)
        return match.group(0) if match else None

    def test_a_live_url_is_advertised(self) -> None:
        # PLAN.md Phase 7 asks for a live URL. A placeholder that looks real
        # would be worse than its absence, so the next test pins this one to
        # captured evidence rather than trusting it.
        assert self._advertised_url(README.read_text())

    def test_the_advertised_url_is_the_one_that_was_smoke_tested(self) -> None:
        # The failure this catches: a stack is redeployed, CloudFront hands out
        # a new domain, and the README keeps pointing at a distribution that no
        # longer exists.
        assert self._advertised_url(README.read_text()) == self._advertised_url(
            SMOKE_EVIDENCE.read_text()
        )

    def test_the_smoke_run_behind_the_claim_passed(self) -> None:
        smoke = SMOKE_EVIDENCE.read_text()
        assert "**All checks passed.**" in smoke
        assert "**FAIL**" not in smoke

    def test_the_smoke_run_covers_the_documented_checklist(self) -> None:
        # Guards the guard: an empty checklist would pass the test above.
        rows = re.findall(r"^\| \d+b? \| ", SMOKE_EVIDENCE.read_text(), re.MULTILINE)
        assert len(rows) >= 11

    def test_readme_no_longer_claims_to_be_undeployed(self) -> None:
        assert "never deployed" not in README.read_text().lower()

    def test_evidence_points_at_the_deployed_checks(self) -> None:
        # docs/EVIDENCE.md covers what the suite proves; it must not go on
        # claiming that nothing has been run against AWS now that something has.
        evidence = EVIDENCE.read_text()
        assert "SMOKE_EVIDENCE.md" in evidence
        assert "Nothing here has been run" not in evidence

    def test_open_checkboxes_carry_an_explanation(self) -> None:
        # Every unticked box should say why, not sit there ambiguously.
        lines = TASKS.read_text().splitlines()
        for index, line in enumerate(lines):
            if line.startswith("- [ ]"):
                follow_on = lines[index + 1] if index + 1 < len(lines) else ""
                assert follow_on.startswith("      "), (
                    f"unticked task has no explanation: {line.strip()}"
                )
