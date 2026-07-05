variable "aws_profile" {
  description = "AWS CLI profile with admin permissions (IAM, S3, Glue, Athena, Budgets)."
  type        = string
  default     = "olx-bootstrap"
}

variable "aws_region" {
  description = "AWS region for all resources."
  type        = string
  default     = "us-east-1"
}

variable "budget_notification_email" {
  description = "Email address that receives the monthly budget alert. Set in terraform.tfvars (gitignored) to keep account-specific data out of version control."
  type        = string
}
