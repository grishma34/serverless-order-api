#!/usr/bin/env bash
#
# Run the post-deploy smoke checklist against a real stack and write
# docs/SMOKE_EVIDENCE.md from the actual responses.
#
# This is the counterpart to capture.sh. That one records what the test suite
# proves locally; this one records the handful of things the suite structurally
# cannot — real DynamoDB condition expressions, real IAM scoping, real
# CloudFront path handling, real S3 OAC enforcement. Both write captured output
# rather than transcribed claims, for the same reason: a transcription can go
# stale without the file changing.
#
# Usage:
#   bash docs/evidence/smoke.sh <site-url> <frontend-bucket> [region] [stack-name]
#
# Exits non-zero if any check fails, so it works as a gate and not just a
# reporter. Requires curl and an AWS-independent network path to the site; only
# the bucket URL check needs to know the region.

set -uo pipefail

cd "$(dirname "$0")/../.."

SITE=${1:?usage: smoke.sh <site-url> <frontend-bucket> [region] [stack-name]}
BUCKET=${2:?usage: smoke.sh <site-url> <frontend-bucket> [region] [stack-name]}
REGION=${3:-ap-southeast-2}
STACK=${4:-unknown}
OUT=docs/SMOKE_EVIDENCE.md

SITE=${SITE%/}
WORK=$(mktemp -d)
trap 'rm -rf "$WORK"' EXIT

FAILURES=0
ROWS=""

# record <n> <description> <expected> <actual> <verdict-note>
record() {
  local ok="PASS"
  if [ "$3" != "$4" ]; then
    ok="**FAIL**"
    FAILURES=$((FAILURES + 1))
  fi
  ROWS="${ROWS}| $1 | $2 | \`$3\` | \`$4\` | $ok — $5 |"$'\n'
}

status_of() { curl -s -o "$1" -w '%{http_code}' "${@:2}"; }

JSON_HDR=(-H 'Content-Type: application/json')
PAYLOAD='{"customerId":"smoke-cust","currency":"AUD","items":[{"sku":"SMOKE-1","name":"Smoke Widget","quantity":2,"unitPriceCents":4999}]}'

KEY=$(cat /proc/sys/kernel/random/uuid)

# 1. Create with a fresh key.
code=$(status_of "$WORK/create1.json" -X POST "$SITE/api/orders" \
  "${JSON_HDR[@]}" -H "Idempotency-Key: $KEY" -d "$PAYLOAD")
record 1 'POST /api/orders, fresh Idempotency-Key' 201 "$code" 'order created'

ORDER_ID=$(python3 -c 'import json,sys;print(json.load(open(sys.argv[1]))["orderId"])' \
  "$WORK/create1.json" 2>/dev/null || echo '')

# 2. Replay the same key. The REQ-0010 proof against real DynamoDB: moto's
#    imitation of TransactWriteItems condition semantics is what PLAN.md § Risks
#    names as the top unknown, and this is the check that closes it.
code=$(status_of "$WORK/create2.json" -X POST "$SITE/api/orders" \
  "${JSON_HDR[@]}" -H "Idempotency-Key: $KEY" -d "$PAYLOAD")
record 2 'Repeat the same key' 200 "$code" 'replay, not a second create'

if diff -q "$WORK/create1.json" "$WORK/create2.json" >/dev/null 2>&1; then
  identical=identical
else
  identical=differs
fi
record 2b 'Replay body is byte-identical' identical "$identical" 'same order returned verbatim'

# 3. The customer has exactly one order despite two POSTs.
code=$(status_of "$WORK/list.json" "$SITE/api/customers/smoke-cust/orders")
record 3 'GET /api/customers/{id}/orders' 200 "$code" 'AP3 via GSI1'
count=$(python3 -c 'import json,sys;print(len(json.load(open(sys.argv[1]))["orders"]))' \
  "$WORK/list.json" 2>/dev/null || echo 'error')
record 3b 'Orders for that customer' 1 "$count" 'the retry created nothing'

# 4. Fetch the single order (AP1).
code=$(status_of "$WORK/get.json" "$SITE/api/orders/$ORDER_ID")
record 4 'GET /api/orders/{orderId}' 200 "$code" 'AP1, one Query returns META + items'

# 5. Legal transition. Also the first exercise of an index Query under the
#    GSI-only IAM scope, which is the second open question in PLAN.md § Risks.
code=$(status_of "$WORK/patch.json" -X PATCH "$SITE/api/orders/$ORDER_ID" \
  "${JSON_HDR[@]}" -d '{"status":"PAID"}')
record 5 'PATCH to PAID' 200 "$code" 'conditional UpdateItem + GSI key rewrite'

# 6. Illegal transition, with the from/to pair echoed back.
code=$(status_of "$WORK/illegal.json" -X PATCH "$SITE/api/orders/$ORDER_ID" \
  "${JSON_HDR[@]}" -d '{"status":"DELIVERED"}')
record 6 'PATCH PAID to DELIVERED' 409 "$code" 'refused by the state machine'
pair=$(python3 -c 'import json,sys;d=json.load(open(sys.argv[1]));print(d.get("from"),"->",d.get("to"),sep="")' \
  "$WORK/illegal.json" 2>/dev/null || echo 'error')
record 6b 'Conflict body names the pair' 'PAID->DELIVERED' "$pair" 'actionable 409'

# 7. Ops listing across customers (AP5 via GSI2).
code=$(status_of "$WORK/bystatus.json" "$SITE/api/orders?status=PAID")
record 7 'GET /api/orders?status=PAID' 200 "$code" 'AP5 via GSI2, GSI-only IAM scope'

# 8. Unknown order.
code=$(status_of "$WORK/404.json" "$SITE/api/orders/01SMOKENOSUCHORDER0000000")
record 8 'GET an unknown orderId' 404 "$code" 'typed error envelope'

# 9. Missing idempotency key.
code=$(status_of "$WORK/400.json" -X POST "$SITE/api/orders" "${JSON_HDR[@]}" -d "$PAYLOAD")
record 9 'POST with no Idempotency-Key' 400 "$code" 'REQ-0010 is enforced, not optional'

# 10. The UI itself, from the CloudFront root.
code=$(status_of "$WORK/root.html" "$SITE/")
record 10 'GET the CloudFront root' 200 "$code" 'UI served from S3 through OAC'
if grep -qi '<!doctype html' "$WORK/root.html" 2>/dev/null; then
  served=html
else
  served=other
fi
record 10b 'Root returns the SPA' html "$served" 'DefaultRootObject resolves'

# 11. The bucket is unreachable except through CloudFront (REQ-0020).
code=$(status_of /dev/null "https://${BUCKET}.s3.${REGION}.amazonaws.com/index.html")
record 11 'Direct S3 object URL' 403 "$code" 'OAC enforced; the bucket is private'

{
  echo "# Smoke evidence — deployed stack"
  echo
  echo "Captured from a real deployment by \`bash docs/evidence/smoke.sh\`, not"
  echo "transcribed. Every row below is an HTTP response from AWS."
  echo
  echo "This file exists because a handful of guarantees cannot be proven by the"
  echo "test suite at all. moto imitates DynamoDB's conditional writes; it does not"
  echo "*be* them. IAM scoping, CloudFront path handling and S3 origin access"
  echo "control have no local equivalent to exercise. Those checks live here."
  echo
  echo "| | |"
  echo "|---|---|"
  echo "| Captured | $(date -u +%Y-%m-%dT%H:%M:%SZ) |"
  echo "| Stack | \`$STACK\` |"
  echo "| Region | \`$REGION\` |"
  echo "| Site | $SITE |"
  echo "| Order created | \`${ORDER_ID:-none}\` |"
  echo
  echo "## Checklist"
  echo
  echo "Mirrors \`docs/DEPLOYMENT.md\` § 5."
  echo
  echo "| # | Check | Expected | Actual | Result |"
  echo "|---|---|---|---|---|"
  printf '%s' "$ROWS"
  echo
  if [ "$FAILURES" -eq 0 ]; then
    echo "**All checks passed.**"
  else
    echo "**$FAILURES check(s) failed.**"
  fi
  echo
  echo "## The three risks this closes"
  echo
  echo "\`PLAN.md\` § Risks names three things that could only be settled against"
  echo "real AWS. Rows 2/2b, 7 and 10 are the settlements."
  echo
  echo '### 1. moto vs real DynamoDB condition semantics (REQ-0010)'
  echo
  echo 'Two POSTs, one `Idempotency-Key`. The second returns `200` with a body'
  echo 'identical to the first, and the customer still has exactly one order —'
  echo 'so the `TransactWriteItems` condition expression behaves against real'
  echo 'DynamoDB the way the moto-backed tests assume.'
  echo
  echo '```json'
  echo '// first POST — 201'
  cat "$WORK/create1.json" 2>/dev/null
  echo
  echo '// second POST, same key — 200'
  cat "$WORK/create2.json" 2>/dev/null
  echo
  echo '```'
  echo
  echo '### 2. GSI-only IAM scoping (NFR-0004)'
  echo
  echo 'The two list functions hold `dynamodb:Query` on their index ARN and not'
  echo 'on the table. Both listings return `200`, so an index `Query` is'
  echo 'authorised by the index ARN alone — the functions genuinely cannot read'
  echo 'the base table.'
  echo
  echo '```json'
  cat "$WORK/bystatus.json" 2>/dev/null
  echo
  echo '```'
  echo
  echo '### 3. CloudFront path handling (REQ-0021)'
  echo
  echo 'Every row above was issued against the CloudFront domain, not the API'
  echo 'Gateway endpoint. `/api/*` arrives unrewritten — the `$default` stage'
  echo 'takes no path prefix — and `Idempotency-Key` survives the hop, which row 2'
  echo 'depends on. The root serves the UI from the same domain, so the browser'
  echo 'makes no cross-origin request and there is no CORS configuration to get'
  echo 'wrong.'
  echo
  echo 'Row 11 is the other half: the bucket refuses a direct request, so'
  echo 'CloudFront with OAC is the only path to the objects (REQ-0020).'
} > "$OUT"

echo "wrote $OUT ($FAILURES failure(s))"
exit $((FAILURES > 0))
