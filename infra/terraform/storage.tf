# S3 bucket, Athena workgroup, and budget. Values transcribed from the live
# resources (aws s3api/athena/budgets get-* output), not from the retired
# bootstrap JSON, so the post-import plan is clean.

resource "aws_s3_bucket" "data" {
  bucket = local.bucket_name
}

resource "aws_s3_bucket_versioning" "data" {
  bucket = aws_s3_bucket.data.id

  versioning_configuration {
    status = "Enabled"
  }
}

resource "aws_s3_bucket_public_access_block" "data" {
  bucket = aws_s3_bucket.data.id

  block_public_acls       = true
  block_public_policy     = true
  ignore_public_acls      = true
  restrict_public_buckets = true
}

resource "aws_athena_workgroup" "olx_data" {
  name        = "olx_data"
  description = "OLX scraper workgroup — scoped result location and scan limits"
  state       = "ENABLED"

  configuration {
    enforce_workgroup_configuration    = true
    publish_cloudwatch_metrics_enabled = true
    bytes_scanned_cutoff_per_query     = 1073741824

    result_configuration {
      output_location = "s3://${local.bucket_name}/athena-results/"
    }
  }
}

resource "aws_budgets_budget" "monthly" {
  name              = "olx-scraper-monthly"
  budget_type       = "COST"
  limit_amount      = "5.0"
  limit_unit        = "USD"
  time_unit         = "MONTHLY"
  time_period_start = "2026-05-01_00:00"

  notification {
    notification_type          = "ACTUAL"
    comparison_operator        = "GREATER_THAN"
    threshold                  = 100
    threshold_type             = "PERCENTAGE"
    subscriber_email_addresses = [var.budget_notification_email]
  }
}
