# Serverless Order API

A serverless REST API for customer orders on AWS — built to demonstrate production
patterns: single-table DynamoDB design with zero scans, idempotent writes that
survive Lambda retries, one-domain delivery via CloudFront, and a fully automated
SAM + GitHub Actions pipeline.

> **Status:** planning complete — implementation follows `PLAN.md` phase by phase.

## Highlights

- **REST API** on AWS Lambda (Python 3.14) + API Gateway HTTP API; static frontend
  in S3 behind the **same CloudFront domain** (`/api/*` → API Gateway) — no CORS.
- **DynamoDB designed from a written list of 6 access patterns** (`docs/DYNAMODB_DESIGN.md`):
  composite keys + 2 sparse GSIs; every query is `GetItem`/`Query` — a test asserts
  `Scan` is never called.
- **Idempotent writes:** order creation uses `TransactWriteItems` with condition
  expressions on a TTL'd idempotency record — a retried Lambda cannot create a
  duplicate; replays return the original order.
- **90% test coverage** with pytest + moto — AWS fully mocked, no live account needed
  to run the suite.
- **Infra as code:** everything in `template.yaml` (AWS SAM); GitHub Actions deploys
  on merge to `main` via OIDC (no stored AWS keys).

## Documentation map

| Doc | Contents |
|---|---|
| `PLAN.md` | Phased build plan (start here to implement) |
| `TASKS.md` | Executable checklist mirroring the plan |
| `CLAUDE.md` | AI-agent operating rules and guardrails |
| `docs/REQUIREMENTS.md` | REQ-####/NFR-#### requirement register |
| `docs/ARCHITECTURE.md` | System diagram + key decisions |
| `docs/DYNAMODB_DESIGN.md` | Access patterns, key design, write integrity |
| `docs/API_SPEC.md` | Endpoints, payloads, state machine, errors |
| `docs/TEST_STRATEGY.md` | Test layers, fixtures, coverage policy |
| `docs/DEPLOYMENT.md` | One-time bootstrap, OIDC, branch protection, smoke checklist |

## Running (once implemented)

```bash
python3.14 -m venv .venv && source .venv/bin/activate
pip install -r requirements-dev.txt   # pulls in src/requirements.txt too
pytest --cov=src --cov-report=term-missing --cov-fail-under=90
sam validate --lint && sam build
```

## Future work (deliberately out of scope)

Cognito auth, payment integration, multi-region/DR, load testing.
