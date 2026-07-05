# IAM principals. Policies transcribed 1:1 from the live inline documents
# (aws iam get-user-policy / get-role-policy), with bucket/account/region
# interpolated. Access keys are deliberately NOT managed here — Terraform
# state must never hold secrets. Create/rotate keys with the aws CLI
# (see infra/README.md).

# ---------------------------------------------------------------- home box --
# Scraper's upload identity: write-only into raw/. No Get, no List, no Delete.

resource "aws_iam_user" "home_box" {
  name = "olx-scraper-home-box"
}

resource "aws_iam_user_policy" "home_box_write_only" {
  name = "olx-home-box-write-only"
  user = aws_iam_user.home_box.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "WriteRawOnly"
        Effect   = "Allow"
        Action   = ["s3:PutObject"]
        Resource = "${aws_s3_bucket.data.arn}/raw/*"
      },
    ]
  })
}

# ----------------------------------------------------------------- analyst --
# Read-only: S3 + Athena query + Glue metadata. Never on the home box.

resource "aws_iam_user" "analyst" {
  name = "olx-scraper-analyst"
}

resource "aws_iam_user_policy" "analyst_read_only" {
  name = "olx-analyst-read-only"
  user = aws_iam_user.analyst.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "S3ReadOnly"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:ListBucket"]
        Resource = [
          aws_s3_bucket.data.arn,
          "${aws_s3_bucket.data.arn}/*",
        ]
      },
      {
        Sid    = "AthenaQuery"
        Effect = "Allow"
        Action = [
          "athena:StartQueryExecution",
          "athena:GetQueryExecution",
          "athena:GetQueryResults",
        ]
        Resource = local.athena_workgroup_arn
      },
      {
        Sid    = "GlueReadOnly"
        Effect = "Allow"
        Action = ["glue:GetDatabase", "glue:GetTable", "glue:GetPartitions"]
        Resource = [
          local.glue_catalog_arn,
          local.glue_database_arn,
          local.glue_tables_arn,
        ]
      },
    ]
  })
}

# ---------------------------------------------------------------- pipeline --
# Runner identity for scripts/pipeline.py (silver/gold transforms): Athena
# execute, S3 CRUD on the bucket (partition deletes + CTAS-style writes),
# Glue metadata CRUD. Mirrors the Step Functions execution policy so routine
# runs never need admin credentials.

resource "aws_iam_user" "pipeline" {
  name = "olx-pipeline"
}

resource "aws_iam_user_policy" "pipeline" {
  name = "olx-pipeline-policy"
  user = aws_iam_user.pipeline.name

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AthenaExecution"
        Effect = "Allow"
        Action = [
          "athena:StartQueryExecution",
          "athena:GetQueryExecution",
          "athena:GetQueryResults",
          "athena:StopQueryExecution",
        ]
        Resource = local.athena_workgroup_arn
      },
      {
        # GetBucketLocation is required by Athena to verify the results
        # bucket; the mirrored Step Functions policy lacked it (latent gap,
        # never exercised because Step Functions was never deployed).
        Sid    = "S3BucketAccess"
        Effect = "Allow"
        Action = [
          "s3:GetBucketLocation",
          "s3:GetObject",
          "s3:PutObject",
          "s3:DeleteObject",
          "s3:ListBucket",
        ]
        Resource = [
          aws_s3_bucket.data.arn,
          "${aws_s3_bucket.data.arn}/*",
        ]
      },
      {
        Sid    = "GlueAccess"
        Effect = "Allow"
        Action = [
          "glue:GetDatabase",
          "glue:GetTable",
          "glue:GetPartitions",
          "glue:CreateTable",
          "glue:UpdateTable",
          "glue:DeleteTable",
        ]
        Resource = [
          local.glue_catalog_arn,
          local.glue_database_arn,
          local.glue_tables_arn,
        ]
      },
    ]
  })
}

# ------------------------------------------------- step functions (vestigial) --
# Created for a Step Functions orchestration that was superseded by the local
# pipeline runner. Kept imported/managed; delete these four resources when
# certain the in-cloud path won't be revived.

resource "aws_iam_role" "stepfn_execution" {
  name = "olx-stepfn-execution-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "states.amazonaws.com" }
        Action    = "sts:AssumeRole"
      },
    ]
  })
}

resource "aws_iam_role_policy" "stepfn_execution" {
  name = "olx-stepfn-execution-policy"
  role = aws_iam_role.stepfn_execution.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid    = "AthenaExecution"
        Effect = "Allow"
        Action = [
          "athena:StartQueryExecution",
          "athena:GetQueryExecution",
          "athena:GetQueryResults",
          "athena:StopQueryExecution",
        ]
        Resource = local.athena_workgroup_arn
      },
      {
        Sid    = "S3BucketAccess"
        Effect = "Allow"
        Action = ["s3:GetObject", "s3:PutObject", "s3:DeleteObject", "s3:ListBucket"]
        Resource = [
          aws_s3_bucket.data.arn,
          "${aws_s3_bucket.data.arn}/*",
        ]
      },
      {
        Sid    = "GlueAccess"
        Effect = "Allow"
        Action = [
          "glue:GetDatabase",
          "glue:GetTable",
          "glue:GetPartitions",
          "glue:CreateTable",
          "glue:UpdateTable",
          "glue:DeleteTable",
        ]
        Resource = [
          local.glue_catalog_arn,
          local.glue_database_arn,
          local.glue_tables_arn,
        ]
      },
    ]
  })
}

resource "aws_iam_role" "eventbridge_stepfn" {
  name = "olx-eventbridge-stepfn-role"

  assume_role_policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Effect    = "Allow"
        Principal = { Service = "events.amazonaws.com" }
        Action    = "sts:AssumeRole"
      },
    ]
  })
}

resource "aws_iam_role_policy" "eventbridge_stepfn" {
  name = "olx-eventbridge-stepfn-policy"
  role = aws_iam_role.eventbridge_stepfn.id

  policy = jsonencode({
    Version = "2012-10-17"
    Statement = [
      {
        Sid      = "StartStepFnExecution"
        Effect   = "Allow"
        Action   = ["states:StartExecution"]
        Resource = "*"
      },
    ]
  })
}
