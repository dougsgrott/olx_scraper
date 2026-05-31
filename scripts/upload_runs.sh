#!/usr/bin/env bash
set -euo pipefail

BUCKET="${BUCKET:?Set BUCKET env var, e.g. export BUCKET=olx-data-123456789012}"

# aws s3 sync requires ListBucket; the home-box policy only grants PutObject.
# cp --recursive traverses the local tree and uploads each file without
# needing to list the S3 destination.
aws s3 cp runs/ "s3://${BUCKET}/raw/" \
    --recursive \
    --exclude '*.tmp' \
    --profile olx-scraper

echo "Upload complete: runs/ -> s3://${BUCKET}/raw/"
