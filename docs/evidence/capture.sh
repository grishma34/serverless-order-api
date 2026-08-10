#!/usr/bin/env bash
#
# Regenerate docs/EVIDENCE.md from real test runs.
#
# The plan asked for a screenshot of the no-scan test. This is captured output
# instead: it is greppable, diffable, regenerable, and cannot be stale without
# the file changing — none of which is true of a PNG.
#
# Usage:  bash docs/evidence/capture.sh
# Run from the repository root with the venv present at .venv/.

set -euo pipefail

cd "$(dirname "$0")/../.."

PY=.venv/bin/python
PYTEST=.venv/bin/pytest
OUT=docs/EVIDENCE.md

if [ ! -x "$PYTEST" ]; then
  echo "error: $PYTEST not found — create the venv first (see readme.md)." >&2
  exit 1
fi

# Strip the pytest node-id prefix so the test names read as claims.
strip_paths() { sed -E 's|tests/unit/[a-z]+/[a-z_]+\.py::||'; }
outcomes() { grep -E "PASSED|FAILED|passed|failed"; }

{
  echo "# Evidence"
  echo
  echo "Captured from real runs, not transcribed. Regenerate with \`bash docs/evidence/capture.sh\`."
  echo
  echo "Every claim in the README maps to a test below. Nothing here has been run"
  echo "against AWS — see \`docs/DEPLOYMENT.md\` § 5 for the checks that require a"
  echo "deployed stack."
  echo
  echo "Captured on: $(date -u +%Y-%m-%dT%H:%M:%SZ) · Python $($PY -V | cut -d' ' -f2)"
  echo
  echo '## "No table scans" — REQ-0012 / NFR-0003'
  echo
  echo 'Two independent checks: a static grep over `src/`, and a botocore call log'
  echo 'recording the DynamoDB operations actually issued. The last test in the list'
  echo 'fires a real `Scan` to prove the detector is not vacuous.'
  echo
  echo '```'
  $PYTEST tests/unit/data/test_no_scan.py -v --no-cov 2>&1 | outcomes | strip_paths
  echo '```'
  echo
  echo '## "A retry cannot create a duplicate" — REQ-0010'
  echo
  echo 'The data-layer proof and the same guarantee as the client experiences it.'
  echo
  echo '```'
  $PYTEST \
    tests/unit/data/test_order_repository.py::TestIdempotentCreate \
    tests/unit/handlers/test_create_order.py::TestReplaySemantics \
    -v --no-cov 2>&1 | outcomes | strip_paths
  echo '```'
  echo
  echo '## State machine — every ordered pair asserted (REQ-0006 / REQ-0007)'
  echo
  echo '```'
  $PYTEST tests/unit/services/test_status_machine.py --no-cov -q 2>&1 | tail -3
  echo "ordered pairs asserted: $(
    $PYTEST tests/unit/services/test_status_machine.py --no-cov -q --co 2>/dev/null \
      | grep -c 'test_every_pair_matches_the_specification'
  )"
  echo '```'
  echo
  echo '## Infrastructure assertions'
  echo
  echo 'The template and pipeline are checked as data: no IAM policy grants Scan,'
  echo 'no workflow reads a static AWS key, and the OIDC trust is pinned to one repo.'
  echo
  echo '```'
  $PYTEST tests/unit/infra --no-cov -q 2>&1 | tail -3
  echo '```'
  echo
  echo '## Coverage — NFR-0001 (gate: 90%)'
  echo
  echo '```'
  $PYTEST -q --cov=src --cov-report=term-missing --cov-fail-under=90 2>&1 | tail -25
  echo '```'
} > "$OUT"

echo "wrote $OUT"

# Badge from the same run, so the README can never advertise a number the suite
# does not actually produce.
$PYTEST -q --cov=src --cov-report=json:coverage.json >/dev/null 2>&1
$PY docs/evidence/make_badge.py coverage.json docs/assets/coverage.svg
rm -f coverage.json
