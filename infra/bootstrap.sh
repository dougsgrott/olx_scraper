#!/usr/bin/env bash
# infra/bootstrap.sh — One-time provisioning of OLX scraper AWS resources.
#
# Run from the repo root:
#   bash infra/bootstrap.sh
#
# Prerequisites:
#   - AWS CLI v2 installed and configured with credentials that have
#     IAM, S3, Glue, Athena, and Budgets permissions.
#   - python3 in PATH (used for inline JSON parsing).
#
# Steps that require human action are marked with <<MANUAL>> banners.
# The script pauses at each one.

set -euo pipefail

# ─── Resolve account and region ───────────────────────────────────────────────
ACCOUNT_ID=$(aws sts get-caller-identity --query Account --output text)
REGION="${AWS_REGION:-us-east-1}"
BUCKET_NAME="olx-data-${ACCOUNT_ID}"

# ─── Budget alert email ───────────────────────────────────────────────────────
NOTIFICATION_EMAIL="${BUDGET_EMAIL:-}"
if [[ -z "$NOTIFICATION_EMAIL" ]]; then
  read -rp "Enter email address for \$5/month Budget alert: " NOTIFICATION_EMAIL
fi

echo ""
echo "==================================================================="
echo "  OLX Scraper — AWS Bootstrap"
echo "  Account  : $ACCOUNT_ID"
echo "  Region   : $REGION"
echo "  Bucket   : $BUCKET_NAME"
echo "  Alert -> : $NOTIFICATION_EMAIL"
echo "==================================================================="
echo ""

# ─── Helper: substitute placeholders in a policy JSON ─────────────────────────
# Usage: substitute_policy <file>
# Replaces BUCKET_NAME, ACCOUNT_ID, REGION with the runtime values.
substitute_policy() {
  sed \
    -e "s|BUCKET_NAME|${BUCKET_NAME}|g" \
    -e "s|ACCOUNT_ID|${ACCOUNT_ID}|g" \
    -e "s|REGION|${REGION}|g" \
    "$1"
}

# ─────────────────────────────────────────────────────────────────────────────
# Step 1 — S3 bucket
# ─────────────────────────────────────────────────────────────────────────────
echo "[1/12] Creating S3 bucket: $BUCKET_NAME"
if aws s3api head-bucket --bucket "$BUCKET_NAME" 2>/dev/null; then
  echo "       Already exists — skipping creation."
else
  if [[ "$REGION" == "us-east-1" ]]; then
    aws s3api create-bucket --bucket "$BUCKET_NAME" --region "$REGION"
  else
    aws s3api create-bucket --bucket "$BUCKET_NAME" --region "$REGION" \
      --create-bucket-configuration LocationConstraint="$REGION"
  fi
  echo "       Created."
fi

# ─────────────────────────────────────────────────────────────────────────────
# Step 2 — Enable bucket versioning
# ─────────────────────────────────────────────────────────────────────────────
echo "[2/12] Enabling bucket versioning..."
aws s3api put-bucket-versioning \
  --bucket "$BUCKET_NAME" \
  --versioning-configuration Status=Enabled
echo "       Done."

# ─────────────────────────────────────────────────────────────────────────────
# Step 3 — Block all public access
# ─────────────────────────────────────────────────────────────────────────────
echo "[3/12] Blocking all public access..."
aws s3api put-public-access-block \
  --bucket "$BUCKET_NAME" \
  --public-access-block-configuration \
    "BlockPublicAcls=true,IgnorePublicAcls=true,BlockPublicPolicy=true,RestrictPublicBuckets=true"
echo "       Done."

# ─────────────────────────────────────────────────────────────────────────────
# Step 4 — IAM user olx-scraper-home-box
# ─────────────────────────────────────────────────────────────────────────────
echo "[4/12] Creating IAM user olx-scraper-home-box..."
if ! aws iam get-user --user-name olx-scraper-home-box &>/dev/null; then
  aws iam create-user --user-name olx-scraper-home-box
  echo "       User created."
else
  echo "       Already exists — skipping creation."
fi

echo "       Attaching inline policy..."
aws iam put-user-policy \
  --user-name olx-scraper-home-box \
  --policy-name olx-home-box-write-only \
  --policy-document "$(substitute_policy infra/iam/home-box-policy.json)"
echo "       Done."

# ─────────────────────────────────────────────────────────────────────────────
# Step 5 — Access key for home-box user
# ─────────────────────────────────────────────────────────────────────────────
echo "[5/12] Creating access key for olx-scraper-home-box..."
KEY_JSON=$(aws iam create-access-key --user-name olx-scraper-home-box)
HB_KEY_ID=$(echo "$KEY_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['AccessKey']['AccessKeyId'])")
HB_SECRET=$(echo "$KEY_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['AccessKey']['SecretAccessKey'])")

echo ""
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║  <<MANUAL>>  Home-box credentials — shown once, save now        ║"
echo "╠══════════════════════════════════════════════════════════════════╣"
echo "║  Write to ~/.aws/credentials on the home box (chmod 600):       ║"
echo "║                                                                  ║"
echo "║  [olx-scraper]                                                   ║"
printf "║  aws_access_key_id     = %-38s║\n" "$HB_KEY_ID"
printf "║  aws_secret_access_key = %-38s║\n" "$HB_SECRET"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""
read -rp "  Press Enter once you have saved these credentials..."

# ─────────────────────────────────────────────────────────────────────────────
# Step 6 — AWS Budget
# ─────────────────────────────────────────────────────────────────────────────
echo "[6/12] Creating AWS Budget (olx-scraper-monthly, \$5/month)..."
aws budgets create-budget \
  --account-id "$ACCOUNT_ID" \
  --budget file://infra/aws-budget.json \
  --notifications-with-subscribers "[{
    \"Notification\": {
      \"NotificationType\": \"ACTUAL\",
      \"ComparisonOperator\": \"GREATER_THAN\",
      \"Threshold\": 100.0,
      \"ThresholdType\": \"PERCENTAGE\"
    },
    \"Subscribers\": [{
      \"SubscriptionType\": \"EMAIL\",
      \"Address\": \"${NOTIFICATION_EMAIL}\"
    }]
  }]" \
  2>/dev/null && echo "       Created." || echo "       Budget may already exist — continuing."

echo ""
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║  <<MANUAL>>  Budget email confirmation required                  ║"
echo "╠══════════════════════════════════════════════════════════════════╣"
printf "║  AWS sent a confirmation to: %-36s║\n" "$NOTIFICATION_EMAIL"
echo "║  Click the link in that email before the alert becomes active.  ║"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
# Step 7 — Glue database
# ─────────────────────────────────────────────────────────────────────────────
echo "[7/12] Creating Glue database olx_data..."
aws glue create-database \
  --database-input '{"Name":"olx_data","Description":"OLX scraper bronze/silver/gold tables"}' \
  2>/dev/null && echo "       Created." || echo "       Already exists — skipping."

# ─────────────────────────────────────────────────────────────────────────────
# Step 8 — Athena workgroup
# ─────────────────────────────────────────────────────────────────────────────
echo "[8/12] Creating Athena workgroup olx_data..."
WORKGROUP_CFG=$(substitute_policy infra/athena/workgroup.json \
  | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin)['Configuration']))")

aws athena create-work-group \
  --name olx_data \
  --configuration "$WORKGROUP_CFG" \
  --description "OLX scraper workgroup — scoped result location and scan limits" \
  2>/dev/null && echo "       Created." || echo "       Already exists — skipping."

# ─────────────────────────────────────────────────────────────────────────────
# Step 9 — IAM role olx-stepfn-execution-role
# ─────────────────────────────────────────────────────────────────────────────
echo "[9/12] Creating IAM role olx-stepfn-execution-role..."
if ! aws iam get-role --role-name olx-stepfn-execution-role &>/dev/null; then
  aws iam create-role \
    --role-name olx-stepfn-execution-role \
    --assume-role-policy-document file://infra/iam/stepfn-execution-role-trust.json
  echo "       Role created."
else
  echo "       Already exists — skipping creation."
fi

echo "       Attaching inline policy..."
aws iam put-role-policy \
  --role-name olx-stepfn-execution-role \
  --policy-name olx-stepfn-execution-policy \
  --policy-document "$(substitute_policy infra/iam/stepfn-execution-role-policy.json)"
echo "       Done."

# ─────────────────────────────────────────────────────────────────────────────
# Step 10 — IAM role olx-eventbridge-stepfn-role
# ─────────────────────────────────────────────────────────────────────────────
echo "[10/12] Creating IAM role olx-eventbridge-stepfn-role..."
if ! aws iam get-role --role-name olx-eventbridge-stepfn-role &>/dev/null; then
  aws iam create-role \
    --role-name olx-eventbridge-stepfn-role \
    --assume-role-policy-document file://infra/iam/eventbridge-stepfn-role-trust.json
  echo "        Role created."
else
  echo "        Already exists — skipping creation."
fi

echo "        Attaching inline policy..."
aws iam put-role-policy \
  --role-name olx-eventbridge-stepfn-role \
  --policy-name olx-eventbridge-stepfn-policy \
  --policy-document file://infra/iam/eventbridge-stepfn-role-policy.json
echo "        Done."

# ─────────────────────────────────────────────────────────────────────────────
# Step 11 — IAM user olx-scraper-analyst
# ─────────────────────────────────────────────────────────────────────────────
echo "[11/12] Creating IAM user olx-scraper-analyst..."
if ! aws iam get-user --user-name olx-scraper-analyst &>/dev/null; then
  aws iam create-user --user-name olx-scraper-analyst
  echo "        User created."
else
  echo "        Already exists — skipping creation."
fi

echo "        Attaching inline policy..."
aws iam put-user-policy \
  --user-name olx-scraper-analyst \
  --policy-name olx-analyst-read-only \
  --policy-document "$(substitute_policy infra/iam/analyst-policy.json)"
echo "        Done."

# ─────────────────────────────────────────────────────────────────────────────
# Step 12 — Access key for analyst user
# ─────────────────────────────────────────────────────────────────────────────
echo "[12/12] Creating access key for olx-scraper-analyst..."
KEY_JSON=$(aws iam create-access-key --user-name olx-scraper-analyst)
AN_KEY_ID=$(echo "$KEY_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['AccessKey']['AccessKeyId'])")
AN_SECRET=$(echo "$KEY_JSON" | python3 -c "import sys,json; print(json.load(sys.stdin)['AccessKey']['SecretAccessKey'])")

echo ""
echo "╔══════════════════════════════════════════════════════════════════╗"
echo "║  <<MANUAL>>  Analyst credentials — shown once, save now         ║"
echo "╠══════════════════════════════════════════════════════════════════╣"
echo "║  Write to ~/.aws/credentials on the analyst machine:            ║"
echo "║                                                                  ║"
echo "║  [olx-analyst]                                                   ║"
printf "║  aws_access_key_id     = %-38s║\n" "$AN_KEY_ID"
printf "║  aws_secret_access_key = %-38s║\n" "$AN_SECRET"
echo "╚══════════════════════════════════════════════════════════════════╝"
echo ""

# ─────────────────────────────────────────────────────────────────────────────
echo ""
echo "==================================================================="
echo "  Bootstrap complete."
echo ""
echo "  Next steps:"
echo "  1. Confirm the Budget email subscription (check $NOTIFICATION_EMAIL)."
echo "  2. Copy home-box credentials to the home box (~/.aws/credentials,"
echo "     profile [olx-scraper], chmod 600)."
echo "  3. Run the acceptance checks in infra/README.md § Verification."
echo "==================================================================="
