"""Guards on the static frontend.

There is no JavaScript test runner in this project and adding one for a page
this small is not worth the toolchain. What these check is the handful of
properties that are load-bearing and easy to break silently — above all that no
absolute API origin creeps in, which is the single change that would reintroduce
CORS and undo REQ-0021.
"""

from __future__ import annotations

import json
import pathlib
import re

import pytest

from services.status_machine import TRANSITIONS
from shared.models import OrderStatus

REPO_ROOT = pathlib.Path(__file__).resolve().parents[3]
FRONTEND = REPO_ROOT / "frontend"

INDEX = FRONTEND / "index.html"
APP = FRONTEND / "app.js"
STYLES = FRONTEND / "styles.css"

# An absolute URL to anywhere that isn't this origin. Data URIs are fine.
ABSOLUTE_URL = re.compile(r"https?://[^\s\"'`)]+")

# Hosts that would mean the browser is talking to a second origin.
CROSS_ORIGIN_MARKERS = ("execute-api", "cloudfront.net", "amazonaws.com")


def frontend_files() -> list[pathlib.Path]:
    return sorted(p for p in FRONTEND.rglob("*") if p.is_file())


def code_of(path: pathlib.Path) -> str:
    """File contents with comments removed.

    The checks below hunt for hostnames and URLs in *code*. Prose that mentions
    a hostname — including the comment in app.js explaining why one must never
    be hardcoded — is not a violation, and matching it would make the rule
    unstatable in its own explanation.

    Block comments go entirely; for line comments only whole-comment lines are
    dropped, so a `//` inside a string literal is left alone.
    """
    text = re.sub(r"/\*.*?\*/", "", path.read_text(), flags=re.DOTALL)
    text = re.sub(r"<!--.*?-->", "", text, flags=re.DOTALL)
    return "\n".join(
        line for line in text.splitlines() if not line.lstrip().startswith(("//", "*"))
    )


class TestFilesExist:
    def test_the_frontend_directory_is_not_empty(self) -> None:
        # Guards the guard: deploy.yml syncs this directory, and an empty glob
        # would make every check below vacuous.
        assert len(frontend_files()) >= 3

    def test_index_html_exists(self) -> None:
        # template.yaml sets DefaultRootObject: index.html — a differently named
        # entry point would serve a 404 at the site root.
        assert INDEX.is_file()

    def test_referenced_assets_exist(self) -> None:
        markup = INDEX.read_text()
        for asset in (STYLES.name, APP.name):
            assert asset in markup, f"index.html does not reference {asset}"
            assert (FRONTEND / asset).is_file()


class TestSameOriginOnly:
    """REQ-0021: one domain for UI and API, so no CORS anywhere."""

    @pytest.mark.parametrize("path", frontend_files(), ids=lambda p: p.name)
    def test_no_file_hardcodes_a_cross_origin_host(self, path: pathlib.Path) -> None:
        contents = code_of(path)
        for marker in CROSS_ORIGIN_MARKERS:
            assert marker not in contents, (
                f"{path.name} references {marker}; the API must be reached at a "
                "relative /api path or the browser makes a cross-origin request"
            )

    def test_no_external_assets_are_loaded(self) -> None:
        # A CDN script or web font would be a third-party origin the CloudFront
        # distribution knows nothing about.
        offenders = [
            f"{path.name}: {url}"
            for path in frontend_files()
            for url in ABSOLUTE_URL.findall(code_of(path))
        ]
        assert not offenders, "external URLs found: " + "; ".join(offenders)

    def test_the_comment_stripper_does_not_hide_real_code(self) -> None:
        # Guards the guard: if code_of() over-stripped, every check above would
        # pass on an empty string.
        stripped = code_of(APP)
        assert "crypto.randomUUID()" in stripped
        assert "const API" in stripped
        assert len(stripped) > len(APP.read_text()) / 2

    def test_the_api_base_is_a_relative_path(self) -> None:
        base = re.search(r'const API = "([^"]+)"', APP.read_text())
        assert base is not None, "could not find the API base constant"
        assert base.group(1) == "/api"

    def test_every_fetch_goes_through_the_api_helper(self) -> None:
        # A stray bare fetch() could target any origin and would bypass the
        # request log and error handling as well.
        source = APP.read_text()
        bare_fetches = re.findall(r"(?<![\w.])fetch\(", source)
        assert len(bare_fetches) == 1, (
            f"expected exactly one fetch() call, inside the api() helper; found {len(bare_fetches)}"
        )

    def test_no_cors_workarounds(self) -> None:
        source = APP.read_text()
        for marker in ("mode: 'cors'", 'mode: "cors"', "no-cors", "Access-Control-"):
            assert marker not in source


class TestIdempotency:
    """REQ-0010 as the client's half of the contract."""

    def test_the_key_is_generated_client_side(self) -> None:
        assert "crypto.randomUUID()" in APP.read_text()

    def test_the_key_is_sent_as_the_documented_header(self) -> None:
        assert '"Idempotency-Key"' in APP.read_text()

    def test_a_failed_create_does_not_mint_a_new_key(self) -> None:
        """The whole point: a retry must reuse the key, or it duplicates.

        Checks that the success path is the only one that retires the key.
        """
        source = APP.read_text()
        retire_calls = re.findall(r"retireKey\(\)", source)
        # One definition, one call site.
        assert len(retire_calls) == 2, "retireKey should be defined once and called once"
        assert "if (!reuseLastKey) retireKey();" in source

    def test_the_replay_button_reuses_the_previous_key(self) -> None:
        assert "reuseLastKey: true" in APP.read_text()


class TestStateMachineDoesNotDrift:
    """The UI's transition map must match the server's table exactly."""

    def _ui_transitions(self) -> dict[str, list[str]]:
        match = re.search(r"const ALLOWED_TRANSITIONS = (\{.*?\});", APP.read_text(), re.DOTALL)
        assert match is not None, "could not find ALLOWED_TRANSITIONS in app.js"
        return json.loads(match.group(1))

    def test_the_ui_covers_every_status(self) -> None:
        assert set(self._ui_transitions()) == {s.value for s in OrderStatus}

    def test_every_ui_transition_matches_the_server(self) -> None:
        ui = self._ui_transitions()
        server = {
            status.value: {target.value for target in targets}
            for status, targets in TRANSITIONS.items()
        }

        for status, targets in ui.items():
            assert set(targets) == server[status], (
                f"frontend offers {sorted(targets)} from {status}, "
                f"server allows {sorted(server[status])}"
            )

    def test_the_ui_offers_no_transition_the_server_would_refuse(self) -> None:
        # Stated separately from the equality check above because this is the
        # direction that produces a confusing 409 for the user.
        ui = self._ui_transitions()
        for status, targets in ui.items():
            allowed = {t.value for t in TRANSITIONS[OrderStatus(status)]}
            assert not set(targets) - allowed


class TestMarkup:
    def test_declares_a_charset_and_viewport(self) -> None:
        markup = INDEX.read_text()
        assert 'charset="utf-8"' in markup
        assert 'name="viewport"' in markup

    def test_has_a_language(self) -> None:
        assert 'lang="en"' in INDEX.read_text()

    def test_every_input_is_labelled(self) -> None:
        # Either a <label for=...> or an aria-label; unlabelled inputs are
        # unusable with a screen reader.
        markup = INDEX.read_text()
        ids = set(re.findall(r'<input[^>]*\bid="([^"]+)"', markup))
        labelled = set(re.findall(r'<label[^>]*\bfor="([^"]+)"', markup))
        assert ids <= labelled, f"unlabelled inputs: {sorted(ids - labelled)}"

    def test_status_announcements_are_live(self) -> None:
        assert 'aria-live="polite"' in INDEX.read_text()

    def test_no_inline_event_handlers(self) -> None:
        # onclick="" attributes would need 'unsafe-inline' if a CSP is ever
        # added; all wiring goes through addEventListener.
        assert not re.search(r"\son[a-z]+=\"", INDEX.read_text())
