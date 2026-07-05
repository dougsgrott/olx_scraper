terraform {
  required_version = ">= 1.7"

  required_providers {
    aws = {
      source  = "hashicorp/aws"
      version = "~> 5.0"
    }
  }
}

provider "aws" {
  profile = var.aws_profile
  region  = var.aws_region
}

data "aws_caller_identity" "current" {}

locals {
  account_id  = data.aws_caller_identity.current.account_id
  bucket_name = "olx-data-${local.account_id}"

  athena_workgroup_arn = "arn:aws:athena:${var.aws_region}:${local.account_id}:workgroup/olx_data"
  glue_catalog_arn     = "arn:aws:glue:${var.aws_region}:${local.account_id}:catalog"
  glue_database_arn    = "arn:aws:glue:${var.aws_region}:${local.account_id}:database/olx_data"
  glue_tables_arn      = "arn:aws:glue:${var.aws_region}:${local.account_id}:table/olx_data/*"
}
