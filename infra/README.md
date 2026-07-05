# infra — OLX Scraper AWS Infrastructure

All AWS resources are managed by **Terraform** (`infra/terraform/`). The recurring
data pipeline (bronze upload, silver/gold transforms) is driven by
**`scripts/pipeline.py`** — no console SQL, no hand-substituted tokens.

The original `bootstrap.sh` + JSON-policy workflow was retired in July 2026; the
existing resources were imported into Terraform state (see git history for the
old files).

## Layout

```
infra/
├── terraform/          ← ALL resource definitions (the source of truth)
│   ├── main.tf         provider, account-derived locals (bucket name)
│   ├── variables.tf    profile, region, budget email
│   ├── storage.tf      S3 bucket, Athena workgroup, budget
│   ├── glue.tf         Glue database + raw/silver/gold table schemas
│   ├── iam.tf          users, roles, inline policies (never access keys)
│   └── outputs.tf      bucket name, workgroup, database, role ARNs
├── silver/catalog_events.sql   INSERT template run by pipeline.py (per dt+region)
└── gold/fact_listings.sql      full-rebuild INSERT run by pipeline.py
```

The `.sql` files are **pure, comment-free statements** (some clients flatten
newlines, turning a leading `--` into a swallow-everything bug); rationale lives
in `docs/`.

## S3 Prefix Layout

Bucket: `olx-data-<account-id>` (region: `us-east-1`, name derived from the
caller's account — never hardcoded anywhere).

```
olx-data-<account-id>/
├── raw/
│   └── spider={catalog,ad}/dt=YYYY-MM-DD/region=<slug>/*.jsonl.gz   ← bronze (delta: new uids)
├── silver/
│   └── catalog_events/dt=YYYY-MM-DD/*.parquet     ← typed; region is a column
├── gold/
│   └── fact_listings/*.parquet                    ← one row per uid; full rebuild
└── athena-results/                                ← workgroup result location
```

Tables (all in Glue database `olx_data`, schemas defined in `terraform/glue.tf`,
partition projection — no crawlers): `raw_catalog`, `silver_catalog_events`,
`fact_listings`. The dataset is a **listings snapshot** — one row per `uid`
([ADR 0006](../docs/adr/0006-snapshot-listings-drop-fingerprint.md)).

## Terraform workflow

State is **local and gitignored** (`terraform/*.tfstate*`) — deliberate for a
solo project; the state file is a critical artifact, keep a copy outside the
repo. `terraform.tfvars` (budget email) is also gitignored.

```bash
cd infra/terraform
terraform init                 # once per machine
terraform plan                 # review any change / detect drift
terraform apply                # human approves
```

Any infra change (schema, policy, workgroup) is an edit here + `apply`. Glue
schema changes are metadata-only (never touch S3 data). **Do not** hand-edit
managed resources in the console/CLI — that shows up as drift on the next plan.

If state is ever lost: re-import with `import {}` blocks (resource IDs are in
git history — `infra/terraform/imports.tf` before its post-import deletion).

## IAM Principals

| Principal | Type | Profile | Purpose |
|-----------|------|---------|---------|
| `olx-scraper-home-box` | user | `olx-scraper` | bronze upload; `s3:PutObject` on `raw/*` ONLY |
| `olx-pipeline` | user | `olx-pipeline` | pipeline.py transforms; S3 CRUD + Athena exec + Glue CRUD |
| `olx-scraper-analyst` | user | `olx-analyst` | read-only S3/Athena/Glue; never on the home box |
| `olx-stepfn-execution-role` | role | — | **vestigial** (Step Functions path superseded by pipeline.py) |
| `olx-eventbridge-stepfn-role` | role | — | **vestigial**; delete both from iam.tf when certain |

Note: the stepfn policy lacks `s3:GetBucketLocation` (Athena needs it); the
pipeline policy fixed this. If the stepfn path is ever revived, copy the fix.

### Access keys — manual by design

Terraform never manages keys (they'd land in plaintext state). Create/rotate
with the CLI:

```bash
# create (shown once — save to ~/.aws/credentials before closing)
aws iam create-access-key --user-name olx-pipeline --profile olx-bootstrap

# rotate: create new, update credentials file, then deactivate + delete old
aws iam list-access-keys --user-name olx-pipeline --profile olx-bootstrap
aws iam update-access-key --user-name olx-pipeline --access-key-id <OLD> --status Inactive --profile olx-bootstrap
aws iam delete-access-key --user-name olx-pipeline --access-key-id <OLD> --profile olx-bootstrap
```

Same pattern for `olx-scraper-home-box` and `olx-scraper-analyst`.

## Verification

```bash
cd infra/terraform && terraform plan     # expect: "No changes"
uv run python scripts/pipeline.py verify # smoke query + silver typing + gold grain
git grep -iE "AKIA[0-9A-Z]{16}"          # expect: no output
```

## What stays manual (by design)

- `terraform apply` approval
- access-key creation, placement, rotation
- budget email confirmation (only if the budget is ever recreated)
- running the pipeline (`docs/runbooks/catalog-transforms.md`)

## Limitations to remember

- Terraform provisions metadata only; it never runs queries or moves data.
- Renaming/recreating the bucket does **not** migrate objects — that's a manual
  copy, then update nothing (the name is account-derived everywhere).
- `pipeline.py` deliberately does not read Terraform outputs: it derives the
  bucket from `sts get-caller-identity` at runtime, so it works on any machine
  with credentials, no Terraform required.
