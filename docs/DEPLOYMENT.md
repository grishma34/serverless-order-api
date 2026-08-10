# Deployment — Serverless Order API

How this project reaches AWS, and the one-time setup that has to happen first.

Nothing here has been executed yet. The templates validate (`sam validate --lint`,
`cfn-lint`) and the application builds (`sam build`), but no AWS resources exist
and no GitHub remote is configured. Everything below is the procedure, not a
record of it having been run.

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

Also create the `production` GitHub environment that `deploy.yml` targets, if
you want a manual approval gate in front of production:

```bash
gh api -X PUT repos/:owner/:repo/environments/production
```

## 3. Branch protection (REQ-0024)

**Not applied — there is no remote yet.** Run this once the repository exists:

```bash
gh api -X PUT repos/:owner/:repo/branches/main/protection \
  --input - <<'JSON'
{
  "required_status_checks": {
    "strict": true,
    "contexts": ["quality-gate", "template"]
  },
  "enforce_admins": true,
  "required_pull_request_reviews": {
    "required_approving_review_count": 1,
    "dismiss_stale_reviews": true
  },
  "restrictions": null,
  "allow_force_pushes": false,
  "allow_deletions": false
}
JSON
```

The two contexts are the job names in `ci.yml`. `strict: true` requires a branch
to be up to date with `main` before merging, so a PR cannot pass against a stale
base and then break `main`.

`enforce_admins: true` matters more than it looks: without it the protection is
advisory for anyone with admin rights, which on a solo project is everyone.

## 4. First application deploy

The pipeline handles production. For a dev stack, deploy by hand:

```bash
sam build
sam deploy --guided --config-env dev
```

`--guided` writes the resolved settings back into `samconfig.toml`. Review that
diff before committing it.

## 5. Post-deploy smoke checklist

Run against the CloudFront URL from the `SiteUrl` output. This is the check that
the local test suite genuinely cannot make — everything below exercises real
DynamoDB and real CloudFront behaviour rather than moto's imitation of it.

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

Two things to watch on the first run, both flagged in `PLAN.md § Risks` and
neither verifiable locally:

- **GSI-only IAM scope.** `ListCustomerOrdersFunction` and
  `ListOrdersByStatusFunction` are granted `dynamodb:Query` on their index ARN
  and not on the table. If a listing returns 500 with an AccessDenied in the
  logs, add the table ARN to that statement.
- **CloudFront path handling.** Routes already carry the `/api` prefix and the
  stage is `$default`, so `/api/*` should reach API Gateway untouched. A 404
  from API Gateway on step 1 means the path arrived rewritten.

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
