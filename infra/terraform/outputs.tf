output "bucket_name" {
  description = "Data lake bucket (bronze/silver/gold + Athena results)."
  value       = aws_s3_bucket.data.bucket
}

output "athena_workgroup" {
  description = "Athena workgroup for all project queries."
  value       = aws_athena_workgroup.olx_data.name
}

output "glue_database" {
  description = "Glue database holding all project tables."
  value       = aws_glue_catalog_database.olx_data.name
}

output "pipeline_user" {
  description = "IAM user for scripts/pipeline.py (create its access key manually)."
  value       = aws_iam_user.pipeline.name
}

output "stepfn_execution_role_arn" {
  description = "Vestigial Step Functions execution role."
  value       = aws_iam_role.stepfn_execution.arn
}

output "eventbridge_stepfn_role_arn" {
  description = "Vestigial EventBridge->Step Functions role."
  value       = aws_iam_role.eventbridge_stepfn.arn
}
