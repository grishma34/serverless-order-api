# CLAUDE.md — Serverless Order API

Operating instructions for AI agents (Claude Code) working in this repository.

## What this project is

A serverless REST API for managing customer orders on AWS:

- **Backend:** Python 3.14 Lambda functions behind API Gateway (HTTP API)
- **Frontend:** Static site in S3, served through CloudFront — one domain for both UI and API (`/api/*` routes to API Gateway)
- **Data:** Single DynamoDB table designed around 6 documented access patterns (see `docs/DYNAMODB_DESIGN.md`) — **no table scans, ever**
- **IaC:** AWS SAM (`template.yaml`) — every resource is defined in the template
- **CI/CD:** GitHub Actions deploys on merge to `main`
- **Tests:** pytest + moto (mocked AWS). Coverage gate: **90%**. Never test against a live AWS account.

## Source of truth documents

| Question | Read this first |
|---|---|
| What are we building, in what order? | `PLAN.md` |
| What must the system do? | `docs/REQUIREMENTS.md` (REQ-#### / NFR-####) |
| How is it structured? | `docs/ARCHITECTURE.md` |
| How is data modeled? | `docs/DYNAMODB_DESIGN.md` |
| What are the endpoints? | `docs/API_SPEC.md` |
| How do we test? | `docs/TEST_STRATEGY.md` |
| What's left to do? | `TASKS.md` |

When code and docs disagree, stop and reconcile — update the doc in the same PR as the code change.

## Repository layout

```
src/
  handlers/        # one module per Lambda handler (thin: parse → call service → respond)
  services/        # business logic (idempotency, validation, state transitions)
  data/            # DynamoDB repository layer (all boto3 calls live here)
  shared/          # response helpers, errors, logging, models
tests/
  unit/            # mirror src/ structure
  conftest.py      # moto fixtures, table factory
frontend/          # static site (HTML/CSS/JS) deployed to S3
template.yaml      # AWS SAM — all infrastructure
.github/workflows/ # ci.yml (test gate), deploy.yml (SAM deploy on merge)
docs/              # design docs listed above
```

## Hard rules (guardrails)

1. **Never use `Scan`** on DynamoDB — not in code, not in tests-as-shortcuts. Every query must map to a documented access pattern in `docs/DYNAMODB_DESIGN.md`. Adding a new query = update that doc first.
2. **Every write is conditional.** `PutItem`/`UpdateItem` must carry a `ConditionExpression` (idempotency / existence checks). A retry must never create a duplicate or double-apply.
3. **No live AWS in tests.** All AWS interaction in tests goes through moto (`@mock_aws`). No real credentials, no network.
4. **Coverage never drops below 90%.** `pytest --cov=src --cov-fail-under=90` must pass before any commit is considered done.
5. **No hand-created infrastructure.** If it isn't in `template.yaml`, it doesn't exist. Never suggest console click-ops.
6. **boto3 only inside `src/data/`.** Handlers and services stay AWS-free so they're unit-testable without mocks.
7. **No secrets in code or template.** Config via environment variables / SAM parameters.
8. **Don't edit `PROJECT_STRUCTURE.md` or `scaffold.sh`** — they're reference material from a separate exercise.

## Conventions

- Python 3.14, type hints everywhere, `ruff` for lint+format (line length 100).
  Local venvs must be created with `python3.14` (see `.python-version`) so dev
  matches the Lambda `python3.14` runtime; the system `python3` is 3.12.
- Handler signature: `def handler(event, context) -> dict` returning API Gateway proxy responses via `shared/responses.py`.
- Errors: raise typed exceptions from services (`OrderNotFound`, `DuplicateRequest`, `InvalidTransition`); a single decorator maps them to HTTP responses.
- DynamoDB items always carry `PK`, `SK`, `entityType`, and ISO-8601 `createdAt`/`updatedAt`.
- Order IDs: ULIDs (sortable by creation time — this is load-bearing for the key design).
- Commit style: `feat:`, `fix:`, `test:`, `docs:`, `infra:` prefixes; one logical change per commit.

## Commands

```bash
# setup
python3.14 -m venv .venv && source .venv/bin/activate
pip install -r requirements.txt -r requirements-dev.txt

# quality gate (run before declaring any task done)
ruff check src tests && ruff format --check src tests
pytest --cov=src --cov-report=term-missing --cov-fail-under=90

# infra
sam validate --lint
sam build
sam deploy --guided        # first deploy only; CI handles the rest
```

## Definition of done (per task in TASKS.md)

- Code + tests written; quality gate passes locally
- Relevant doc updated if behavior/design changed
- `TASKS.md` checkbox ticked in the same commit
