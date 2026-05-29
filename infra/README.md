# infra — OLX Scraper AWS Infrastructure

One-time setup of the cloud-side foundation. All resources are created by a human running `infra/bootstrap.sh`; the JSON files here are the source of truth for policies, workgroup configuration, and budget definition.

## S3 Prefix Layout

Bucket: `olx-data-<account-id>`  (region: `us-east-1`)

```
olx-data-<account-id>/
├── raw/
│   └── spider={catalog,ad}/
│       └── dt=YYYY-MM-DD/
│           └── region=<slug>/
│               └── *.jsonl.gz          ← bronze; immutable snapshot encounters
├── silver/
│   └── <table>/
│       └── dt=YYYY-MM-DD/
│           └── *.parquet               ← typed, deduped; rebuilt daily from bronze
├── gold/
│   └── <table>/
│       └── *.parquet                   ← analyst-facing; rebuilt daily from silver
└── athena-results/
    └── <query-execution-id>/           ← Athena workgroup result location
        └── *.csv / *.metadata
```

Tables expected in each layer:

| Layer  | Table                   | Notes                                    |
|--------|-------------------------|------------------------------------------|
| raw    | `raw_catalog`           | All catalog spider encounters            |
| raw    | `raw_ad`                | All ad spider encounters                 |
| silver | `silver_catalog_events` | Deduped catalog versions                 |
| silver | `silver_ads`            | Deduped ad details                       |
| gold   | `fact_listing_versions` | One row per (uid, fingerprint) pair      |
| gold   | `dim_location`          | Location dimension enriched from raw ad  |

All Glue tables live in the `olx_data` database. Partition projection (configured per table in issues #0003+) makes Glue crawlers unnecessary.

## Resource Map

| File | AWS Resource |
|------|-------------|
| `iam/home-box-policy.json` | Inline policy on IAM user `olx-scraper-home-box` |
| `iam/stepfn-execution-role-trust.json` | Trust policy for IAM role `olx-stepfn-execution-role` |
| `iam/stepfn-execution-role-policy.json` | Permission policy for `olx-stepfn-execution-role` |
| `iam/eventbridge-stepfn-role-trust.json` | Trust policy for IAM role `olx-eventbridge-stepfn-role` |
| `iam/eventbridge-stepfn-role-policy.json` | Permission policy for `olx-eventbridge-stepfn-role` |
| `iam/analyst-policy.json` | Inline policy on IAM user `olx-scraper-analyst` |
| `athena/workgroup.json` | Athena workgroup `olx_data` |
| `aws-budget.json` | AWS Budget `olx-scraper-monthly` ($5/month actual-spend alert) |

### Placeholder tokens in JSON files

The JSON files use three literal tokens that `bootstrap.sh` substitutes at runtime:

| Token | Resolved to |
|-------|------------|
| `BUCKET_NAME` | `olx-data-<account-id>` |
| `ACCOUNT_ID` | 12-digit AWS account ID |
| `REGION` | AWS region (default `us-east-1`) |

> **Do not edit the resolved values into the JSON files.** Keep the token form so the files are portable and contain no account-specific data.

## IAM Principals

| Principal | Type | Profile name | Lives on |
|-----------|------|-------------|----------|
| `olx-scraper-home-box` | IAM user | `olx-scraper` | Home box `~/.aws/credentials` |
| `olx-scraper-analyst` | IAM user | `olx-analyst` | Analyst machine `~/.aws/credentials` |
| `olx-stepfn-execution-role` | IAM role | — | Assumed by `states.amazonaws.com` |
| `olx-eventbridge-stepfn-role` | IAM role | — | Assumed by `events.amazonaws.com` |

### Home-box permissions (write-only)
`s3:PutObject` on `raw/*` — no `GetObject`, no `ListBucket`, no `DeleteObject`.

### Analyst permissions (read-only)
`s3:GetObject` + `s3:ListBucket` on the whole bucket; Athena query in `olx_data` workgroup; Glue read on `olx_data` database. **Never place analyst credentials on the home box.**

### Step Functions execution role
Full S3 CRUD on the bucket (CTAS overwrites need `DeleteObject`); Athena execute + read on `olx_data` workgroup; Glue CRUD on `olx_data` database (CTAS needs `CreateTable`/`UpdateTable`/`DeleteTable`).

### EventBridge → Step Functions role
`states:StartExecution` on `*` (placeholder until the state machine ARN is known from issue #0005). **Scope this down once the state machine exists.**

## Running the Bootstrap

Prerequisites: AWS CLI v2, `python3` in PATH, credentials with IAM + S3 + Glue + Athena + Budgets permissions.

```bash
# From the repo root:
bash infra/bootstrap.sh
```

The script is largely idempotent — re-running skips already-existing resources (buckets, users, roles, databases, workgroups). Access keys are **not** idempotent; running twice creates a second key pair.

Two manual steps require human action:

1. **Budget email confirmation** — AWS sends a SNS subscription confirmation email; click the link.
2. **Credential placement** — Copy the printed access keys to `~/.aws/credentials` on the respective machines. The `SecretAccessKey` is shown only once.

## Verification

Run these after `bootstrap.sh` completes. All `aws` commands use the `olx-bootstrap` profile (the admin credentials used during the bootstrap). The home-box and analyst checks use their own profiles and are noted separately.

```bash
# Set once for this shell session — every aws command below inherits it.
export AWS_PROFILE=olx-bootstrap
export AWS_REGION=us-east-1

BUCKET=olx-data-$(aws sts get-caller-identity --query Account --output text)

# 1. Bucket versioning + public-access-block
aws s3api get-bucket-versioning --bucket "$BUCKET"
# Expected: {"Status": "Enabled"}

aws s3api get-public-access-block --bucket "$BUCKET"
# Expected: all four BlockPublic* fields true

# 2. Glue database
aws glue get-database --name olx_data
# Expected: {"Database": {"Name": "olx_data", ...}}

# 3. Athena workgroup
aws athena get-work-group --work-group olx_data
# Expected: workgroup with ResultConfiguration.OutputLocation and BytesScannedCutoffPerQuery

# 4. Trivial Athena query
EXEC_ID=$(aws athena start-query-execution \
  --query-string "SELECT 1" \
  --work-group olx_data \
  --query 'QueryExecutionId' --output text)
sleep 5
aws athena get-query-execution --query-execution-id "$EXEC_ID" \
  --query 'QueryExecution.Status.State' --output text
# Expected: SUCCEEDED

# 5. No credentials in version control
git grep -iE "AKIA[0-9A-Z]{16}"
# Expected: no output
```

**Home-box write test** — run on the home box after placing credentials there:

```bash
BUCKET=olx-data-<account-id>   # replace with your 12-digit account ID

echo "test" > /tmp/test.txt
aws s3 cp /tmp/test.txt "s3://${BUCKET}/raw/test.txt" --profile olx-scraper   # should succeed
aws s3 ls "s3://${BUCKET}/" --profile olx-scraper                              # should return AccessDenied
aws s3 rm "s3://${BUCKET}/raw/test.txt" --profile olx-scraper                  # should return AccessDenied
```

**Analyst read test** — run on the analyst machine after placing credentials there:

```bash
BUCKET=olx-data-<account-id>

aws s3 ls "s3://${BUCKET}/raw/" --profile olx-analyst                          # should succeed
aws s3 cp /tmp/test.txt "s3://${BUCKET}/raw/x" --profile olx-analyst           # should return AccessDenied
```

## Credential Rotation

### Rotate home-box access key

```bash
OLD_KEY=$(aws iam list-access-keys --user-name olx-scraper-home-box \
  --query 'AccessKeyMetadata[0].AccessKeyId' --output text)

# Create new key (shown once — save before deactivating old)
aws iam create-access-key --user-name olx-scraper-home-box

# Update ~/.aws/credentials on the home box, then deactivate + delete old key:
aws iam update-access-key --user-name olx-scraper-home-box \
  --access-key-id "$OLD_KEY" --status Inactive
aws iam delete-access-key --user-name olx-scraper-home-box \
  --access-key-id "$OLD_KEY"
```

Repeat the same pattern for `olx-scraper-analyst`.

## Notes

- **No Terraform state** — the project scope (~12 AWS resources) does not justify a state backend. If the scope expands, the JSON files here are a clean starting point for `aws_iam_*`, `aws_s3_bucket`, `aws_glue_catalog_database`, `aws_athena_workgroup`, and `aws_budgets_budget` Terraform resources.
- **No Glue crawlers** — all tables use partition projection configured per-table in issues #0003+.
- **Athena-results prefix** — using a prefix in the same bucket rather than a dedicated bucket keeps the bucket policy simple and costs the same.
