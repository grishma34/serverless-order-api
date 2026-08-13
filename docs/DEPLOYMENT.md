# Deployment — Serverless Order API

How this project reaches AWS, and the one-time setup that has to happen first.

All of it has been run. This is both the procedure and the record.

| Step | Status |
|---|---|
| 1. OIDC bootstrap | Deployed — `serverless-order-api-bootstrap`, `ap-southeast-2` |
| 2. Repository variables | `AWS_DEPLOY_ROLE_ARN` and `AWS_REGION` set; `production` environment locked to `main` |
| 3. Branch protection | Applied to `main` — see the note in § 3 on the review requirement |
| 4. Application stack | Deployed — `serverless-order-api-prod`, by the pipeline |
| 5. Smoke checklist | Run against prod, 15/15 — [`SMOKE_EVIDENCE.md`](SMOKE_EVIDENCE.md) |

A `dev` stack was hand-deployed first, smoke-checked, and then torn down once
the pipeline could deliver prod — § 4 is still the procedure for standing one
up. Two environments is two CloudFront distributions for a single demo.

Account `630300237441`, region `ap-southeast-2`. The commands below are the ones
that were issued, so they can be re-run against a different account unchanged.

## Shape

```
bootstrap/github-oidc.yaml   deployed once, by hand → OIDC trust + deploy role
template.yaml                deployed by the pipeline → the application stack
```

The split is not cosmetic. The deploy role is what creates the application
stack, so it cannot be part of that stack — the first deploy would need the role
that the deploy is supposed to create.

## 1. One-time bootstrap (manual, admin credentials)

```bash
aws cloudformation deploy \
  --template-file bootstrap/github-oidc.yaml \
  --stack-name serverless-order-api-bootstrap \
  --capabilities CAPABILITY_NAMED_IAM \
  --region ap-southeast-2 \
  --parameter-overrides \
      GitHubOrg=<your-github-org-or-username> \
      GitHubRepo=serverless-order-api \
      CreateOIDCProvider=true
```

Set `CreateOIDCProvider=false` if the account already has the GitHub provider —
an account may only have one, and a second create fails the stack. To check:

```bash
aws iam list-open-id-connect-providers \
  | grep -q token.actions.githubusercontent.com && echo exists || echo absent
```

`GitHubOrgId` and `GitHubRepoId` are the numeric ids GitHub embeds in the OIDC
subject. Do not assemble the subject by hand — ask GitHub for it:

```bash
gh api repos/:owner/:repo/actions/oidc/customization/sub --jq .sub_claim_prefix
# repo:<org>@<orgId>/<repo>@<repoId>

gh api repos/:owner/:repo --jq '"GitHubOrgId=\(.owner.id) GitHubRepoId=\(.id)"'
```

Pinning ids rather than names is the stronger choice regardless: a renamed or
deleted repository cannot be impersonated by a new one that later claims the
same name.

Then read the role ARN back out:

```bash
aws cloudformation describe-stacks \
  --stack-name serverless-order-api-bootstrap \
  --query "Stacks[0].Outputs[?OutputKey=='DeployRoleArn'].OutputValue" \
  --output text
```

## 2. Repository configuration

| Name | Kind | Value |
|---|---|---|
| `AWS_DEPLOY_ROLE_ARN` | Variable | The `DeployRoleArn` output above |
| `AWS_REGION` | Variable | e.g. `ap-southeast-2` (defaults if unset) |

```bash
gh variable set AWS_DEPLOY_ROLE_ARN --body "arn:aws:iam::<account>:role/serverless-order-api-github-deploy"
gh variable set AWS_REGION --body "ap-southeast-2"
```

These are **variables, not secrets**. A role ARN is not sensitive, and the
project holds no long-lived AWS credentials anywhere — that is the point of
REQ-0023. If you ever find yourself adding `AWS_ACCESS_KEY_ID` to this
repository, something has gone wrong; `tests/unit/infra/test_workflows.py`
fails the build if a workflow starts reading one.

### The `production` environment is load-bearing, not decorative

`deploy.yml`'s deploy job declares `environment: production`, and that one line
changes the OIDC token. **GitHub swaps the `sub` claim depending on the job:**

| The job | Presents `sub` |
|---|---|
| references an environment | `repo:ORG/REPO:environment:production` |
| references no environment | `repo:ORG/REPO:ref:refs/heads/main` |

The trust policy matches the first form, because that is what this pipeline
actually sends. Getting this wrong is silent until a real deploy: the template
is valid, the workflow is valid, and they disagree only when STS compares the
claim. The first production deploy here failed exactly that way —
`Not authorized to perform sts:AssumeRoleWithWebIdentity`, with no hint as to
which side was wrong. `test_the_trust_subject_matches_what_deploy_presents`
now reads both files together and fails locally instead.

The consequence is that **the subject no longer names a branch**, so the
"only `main` may deploy" guarantee has to come from somewhere else — the
environment's deployment-branch policy:

```bash
gh api -X PUT repos/:owner/:repo/environments/production --input - <<'JSON'
{"deployment_branch_policy": {"protected_branches": false, "custom_branch_policies": true}}
JSON
gh api -X POST repos/:owner/:repo/environments/production/deployment-branch-policies -f name=main
```

Both halves are needed and neither is sufficient alone. IAM decides *what may be
assumed* (only a job targeting `production`); GitHub decides *who may target it*
(only `main`). Worth being honest about the trade: part of this boundary now
lives in repository settings rather than in version-controlled CloudFormation,
which runs against the grain of the rest of this project. The environment is
also where a required-reviewer gate would go if this ever needed one.

## 3. Branch protection (REQ-0024)

Applied. This is what was set:

```bash
gh api -X PUT repos/:owner/:repo/branches/main/protection \
  --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["quality-gate", "template"]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": null,
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON
```

The two contexts are the job names in `ci.yml`. `strict: true` requires a branch
to be up to date with `main` before merging, so a PR cannot pass against a stale
base and then break `main`. With required checks in force, a direct push to
`main` is refused — no commit can carry a passing check before it exists — so
changes go through a pull request whether or not a review is demanded.

`enforce_admins: true` matters more than it looks: without it the protection is
advisory for anyone with admin rights, which on a solo project is everyone.

**`required_pull_request_reviews` is deliberately null.** The obvious setting is
`required_approving_review_count: 1`, and on a team it would be right. Here it
combines with `enforce_admins: true` into a repository where nothing can ever be
merged: GitHub does not let you approve your own pull request, and there is no
second maintainer to ask. The choice is between a rule that is enforced and a
rule that has to be switched off the first time it binds — and a protection you
routinely disable protects nothing. Set the count to 1 the moment a second
maintainer exists; that is the change this line is waiting on, not an oversight.

## 4. First application deploy

The pipeline handles production. For a dev stack, deploy by hand:

```bash
sam build
sam deploy --config-env dev
```

`samconfig.toml` already carries the resolved settings for both environments, so
`--guided` is only needed when targeting a new account. It writes its answers
back into that file — review the diff before committing it.

Note that `sam build` needs a Python matching the Lambda runtime on `PATH`
(`python3.14`, per `.python-version`), and the SAM CLI is not a project
dependency — install it separately (`pip install aws-sam-cli` in its own venv).

Read the outputs back out; the smoke script takes them as arguments:

```bash
aws cloudformation describe-stacks --stack-name serverless-order-api-dev \
  --region ap-southeast-2 --query 'Stacks[0].Outputs' --output table
```

A hand-deployed stack has no pipeline behind it, so the frontend has to be
published the way `deploy.yml` does it for prod:

```bash
aws s3 sync frontend/ "s3://$(...FrontendBucketName)/" --delete
aws cloudfront create-invalidation --distribution-id "$(...DistributionId)" --paths '/*'
```

## 5. Post-deploy smoke checklist

**Run — 15/15 against `serverless-order-api-prod`.** The captured responses are in
[`SMOKE_EVIDENCE.md`](SMOKE_EVIDENCE.md), written by the script rather than
pasted:

```bash
bash docs/evidence/smoke.sh \
  https://<distribution>.cloudfront.net \
  serverless-order-api-dev-frontend-<account> \
  ap-southeast-2 \
  serverless-order-api-dev
```

It exits non-zero if any check fails, so it is a gate and not just a reporter.
The equivalent by hand, against the CloudFront URL from the `SiteUrl` output —
this is the check the local suite genuinely cannot make, since everything below
exercises real DynamoDB and real CloudFront rather than moto's imitation:

```bash
SITE=https://<distribution>.cloudfront.net
KEY=$(uuidgen)

# 1. Create → expect 201
curl -si -X POST "$SITE/api/orders" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $KEY" \
  -d '{"customerId":"cust-1","currency":"AUD",
       "items":[{"sku":"W-1","name":"Widget","quantity":2,"unitPriceCents":4999}]}'

# 2. Replay the SAME key → expect 200 and a byte-identical body.
#    This is the REQ-0010 check against real DynamoDB condition expressions;
#    PLAN.md § Risks flags moto's TransactWriteItems semantics as the top
#    unknown, and this is what resolves it.
curl -si -X POST "$SITE/api/orders" \
  -H "Content-Type: application/json" \
  -H "Idempotency-Key: $KEY" \
  -d '{"customerId":"cust-1","currency":"AUD",
       "items":[{"sku":"W-1","name":"Widget","quantity":2,"unitPriceCents":4999}]}'

# 3. Customer listing shows exactly one order
curl -s "$SITE/api/customers/cust-1/orders"

# 4. Transition → expect 200
curl -si -X PATCH "$SITE/api/orders/<orderId>" \
  -H "Content-Type: application/json" -d '{"status":"PAID"}'

# 5. Illegal transition → expect 409 with from/to in the body
curl -si -X PATCH "$SITE/api/orders/<orderId>" \
  -H "Content-Type: application/json" -d '{"status":"DELIVERED"}'

# 6. Direct S3 access is refused → expect 403
curl -si "https://<bucket>.s3.<region>.amazonaws.com/index.html"
```

Two things were worth watching on the first run, both flagged in
`PLAN.md § Risks` and neither verifiable locally. **Both held**, and the
failure modes are recorded here because they are what to look for if a future
deploy regresses:

- **GSI-only IAM scope.** `ListCustomerOrdersFunction` and
  `ListOrdersByStatusFunction` are granted `dynamodb:Query` on their index ARN
  and not on the table. Both listings returned `200`, so an index `Query` is
  authorised by the index ARN alone and the tighter scope stands. If a listing
  ever returns 500 with an AccessDenied in the logs, add the table ARN to that
  statement.
- **CloudFront path handling.** Routes already carry the `/api` prefix and the
  stage is `$default`, so `/api/*` reaches API Gateway untouched — confirmed,
  including that `Idempotency-Key` survives the hop, which the replay check
  depends on. A 404 from API Gateway on step 1 would mean the path arrived
  rewritten.

## 6. When role assumption is refused

```
##[error]Could not assume role with OIDC: Not authorized to perform sts:AssumeRoleWithWebIdentity
```

STS returns this same message whatever part of the trust policy failed to
match, and the GitHub Actions log never shows the token, so the error on its own
cannot tell you which half is wrong. **Do not guess — CloudTrail recorded the
subject that was actually presented:**

```bash
aws cloudtrail lookup-events \
  --lookup-attributes AttributeKey=EventName,AttributeValue=AssumeRoleWithWebIdentity \
  --region ap-southeast-2 --max-results 5 \
  --query 'Events[].CloudTrailEvent' --output text \
  | python3 -c 'import json,sys;[print(json.loads(l)["userIdentity"]["userName"]) for l in sys.stdin.read().split("\t") if l.strip()]'
```

`userName` on a `WebIdentityUser` event *is* the `sub` claim. Compare it to the
trust policy and the mismatch is immediate:

```bash
aws iam get-role --role-name serverless-order-api-github-deploy \
  --query 'Role.AssumeRolePolicyDocument.Statement[0].Condition.StringEquals' --output json
```

Both deploy failures on this project were diagnosed this way, after two rounds
of plausible-but-wrong guessing:

| Presented | Trust policy said | Wrong because |
|---|---|---|
| `...:environment:production` | `...:ref:refs/heads/main` | the job declares `environment:`, which changes the claim |
| `repo:grishma34@143146045/serverless-order-api@1329772857:...` | `repo:grishma34/serverless-order-api:...` | GitHub's default subject embeds immutable numeric ids |

Two minutes of CloudTrail would have replaced both rounds of guessing. That is
the lesson worth keeping from this section.

## 7. When the deploy role is missing a permission

A hand deploy proves the template. It does **not** prove the pipeline, because
`sam deploy` from a laptop runs as an admin while the pipeline runs as the
scoped deploy role. Every permission gap is invisible until the pipeline runs.

The first production deploy that got past role assumption rolled back on:

```
Insufficient permissions to enable logging.
... not authorized to perform: logs:CreateLogDelivery
```

`ApiAccessLogGroup` had already been created successfully — creating the log
group is not what needs permission. `AccessLogSettings` on an HTTP API makes
API Gateway set up a CloudWatch Logs *delivery* on the caller's behalf, and it
checks the **caller** for `logs:CreateLogDelivery` and friends. That is the
`ManageAccessLogDelivery` statement in `bootstrap/github-oidc.yaml`.

Read failures out of the stack events rather than the workflow log — the
workflow only reports that the deploy failed:

```bash
aws cloudformation describe-stack-events --stack-name serverless-order-api-prod \
  --region ap-southeast-2 \
  --query 'StackEvents[?ResourceStatus==`CREATE_FAILED`].[LogicalResourceId,ResourceStatusReason]' \
  --output text
```

### Cleaning up after a failed create

A stack that fails its **first** create lands in `ROLLBACK_COMPLETE`, which
cannot be updated — it has to be deleted before the next attempt. And because
`Environment=prod` sets `DeletionPolicy: Retain` on the table and the bucket,
those two survive the rollback and keep their deterministic names, so the next
create fails again on a name collision until they are removed by hand:

```bash
aws cloudformation delete-stack --stack-name serverless-order-api-prod
aws cloudformation wait stack-delete-complete --stack-name serverless-order-api-prod
aws s3 rb s3://serverless-order-api-prod-frontend-<account> --force
aws dynamodb delete-table --table-name serverless-order-api-prod-orders
```

**Check what you are deleting first.** That `Retain` policy exists to stop a
stack teardown taking real order data with it, and this cleanup deliberately
steps around it. It is only safe when the stack never finished its first create,
so the table has never been written to — confirm with `describe-table` and
`list-objects-v2` before running any of the above. On a stack that has ever
served traffic, this sequence destroys production data.

## Rollback

```bash
# What changed, and when
aws cloudformation describe-stack-events --stack-name serverless-order-api-prod

# Redeploy a known-good commit
git revert <bad-commit> && git push
```

Reverting and pushing is preferred over a console rollback: it keeps the stack
and the repository in agreement, which is the whole premise of REQ-0022.

`DeletionPolicy: Retain` applies to the table and the frontend bucket when
`Environment=prod`, so deleting the production stack does **not** delete the
order data. Those two resources have to be removed by hand, deliberately.
