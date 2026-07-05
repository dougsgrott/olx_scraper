# Glue database + tables. These resources ARE the table DDL now (the old
# infra/glue/*.sql files are retired); schema changes happen here via
# `terraform apply` (Glue UpdateTable — metadata only, never touches S3 data).
#
# Snapshot model (ADR 0006): no watched_state_fingerprint column anywhere.
# The first apply after import removes it from raw_catalog and
# silver_catalog_events — the schema migration the runbook had left pending.
#
# `$${dt}` / `$${region}` are literal ${dt}/${region} for Athena partition
# projection, escaped from HCL interpolation.

resource "aws_glue_catalog_database" "olx_data" {
  name        = "olx_data"
  description = "OLX scraper bronze/silver/gold tables"
}

locals {
  raw_catalog_columns = [
    { name = "uid", type = "string" },
    { name = "code", type = "string" },
    { name = "title", type = "string" },
    { name = "url", type = "string" },
    { name = "date", type = "string" },
    { name = "location", type = "string" },
    { name = "neighbourhood", type = "string" },
    { name = "municipality", type = "string" },
    { name = "uf", type = "string" },
    { name = "ddd", type = "string" },
    { name = "pricing", type = "string" },
    { name = "old_price", type = "string" },
    { name = "details", type = "string" },
    { name = "badges", type = "string" },
    { name = "characteristics", type = "string" },
    { name = "real_estate_type", type = "string" },
    { name = "condominio", type = "string" },
    { name = "iptu", type = "string" },
    { name = "size", type = "string" },
    { name = "rooms", type = "string" },
    { name = "bathrooms", type = "string" },
    { name = "garage_spaces", type = "string" },
    { name = "category_name", type = "string" },
    { name = "professional_ad", type = "int" },
    { name = "is_featured", type = "int" },
    { name = "fixed_on_top", type = "int" },
    { name = "price_reduction_badge", type = "int" },
    { name = "has_real_estate_highlight", type = "int" },
    { name = "url_is_scraped", type = "int" },
    { name = "url_scraped_date", type = "string" },
    { name = "scraped_date", type = "string" },
    { name = "uploaded_to_cloud", type = "int" },
  ]

  silver_catalog_events_columns = [
    { name = "uid", type = "string" },
    { name = "code", type = "string" },
    { name = "title", type = "string" },
    { name = "url", type = "string" },
    { name = "listing_ts", type = "timestamp" },
    { name = "scraped_ts", type = "timestamp" },
    { name = "price_brl", type = "decimal(12,2)" },
    { name = "old_price_brl", type = "decimal(12,2)" },
    { name = "condominio_brl", type = "decimal(12,2)" },
    { name = "iptu_brl", type = "decimal(12,2)" },
    { name = "size_m2", type = "decimal(10,2)" },
    { name = "rooms", type = "int" },
    { name = "bathrooms", type = "int" },
    { name = "garage_spaces", type = "int" },
    { name = "real_estate_type", type = "string" },
    { name = "category_name", type = "string" },
    { name = "location", type = "string" },
    { name = "neighbourhood", type = "string" },
    { name = "municipality", type = "string" },
    { name = "uf", type = "string" },
    { name = "ddd", type = "string" },
    { name = "region", type = "string" },
    { name = "professional_ad", type = "int" },
    { name = "is_featured", type = "int" },
    { name = "price_reduction_badge", type = "int" },
    { name = "has_real_estate_highlight", type = "int" },
  ]

  fact_listings_columns = [
    { name = "uid", type = "string" },
    { name = "price_brl", type = "decimal(12,2)" },
    { name = "old_price_brl", type = "decimal(12,2)" },
    { name = "condominio_brl", type = "decimal(12,2)" },
    { name = "iptu_brl", type = "decimal(12,2)" },
    { name = "size_m2", type = "decimal(10,2)" },
    { name = "price_per_m2", type = "decimal(12,2)" },
    { name = "rooms", type = "int" },
    { name = "bathrooms", type = "int" },
    { name = "garage_spaces", type = "int" },
    { name = "real_estate_type", type = "string" },
    { name = "category_name", type = "string" },
    { name = "title", type = "string" },
    { name = "url", type = "string" },
    { name = "region", type = "string" },
    { name = "neighbourhood", type = "string" },
    { name = "municipality", type = "string" },
    { name = "uf", type = "string" },
    { name = "first_seen_at", type = "timestamp" },
  ]
}

resource "aws_glue_catalog_table" "raw_catalog" {
  name          = "raw_catalog"
  database_name = aws_glue_catalog_database.olx_data.name
  table_type    = "EXTERNAL_TABLE"
  owner         = "hadoop"

  parameters = {
    "EXTERNAL"                    = "TRUE"
    "has_encrypted_data"          = "false"
    "projection.enabled"          = "true"
    "projection.dt.type"          = "date"
    "projection.dt.format"        = "yyyy-MM-dd"
    "projection.dt.range"         = "2026-01-01,NOW"
    "projection.dt.interval"      = "1"
    "projection.dt.interval.unit" = "DAYS"
    "projection.region.type"      = "injected"
    "storage.location.template"   = "s3://${local.bucket_name}/raw/spider=catalog/dt=$${dt}/region=$${region}/"
  }

  partition_keys {
    name = "dt"
    type = "string"
  }
  partition_keys {
    name = "region"
    type = "string"
  }

  storage_descriptor {
    location          = "s3://${local.bucket_name}/raw/spider=catalog"
    input_format      = "org.apache.hadoop.mapred.TextInputFormat"
    output_format     = "org.apache.hadoop.hive.ql.io.HiveIgnoreKeyTextOutputFormat"
    number_of_buckets = -1

    ser_de_info {
      serialization_library = "org.openx.data.jsonserde.JsonSerDe"
      parameters = {
        "serialization.format"  = "1"
        "ignore.malformed.json" = "true"
      }
    }

    dynamic "columns" {
      for_each = local.raw_catalog_columns
      content {
        name = columns.value.name
        type = columns.value.type
      }
    }
  }

  lifecycle {
    ignore_changes = [parameters["transient_lastDdlTime"]]
  }
}

resource "aws_glue_catalog_table" "silver_catalog_events" {
  name          = "silver_catalog_events"
  database_name = aws_glue_catalog_database.olx_data.name
  table_type    = "EXTERNAL_TABLE"
  owner         = "hadoop"

  parameters = {
    "EXTERNAL"                    = "TRUE"
    "parquet.compression"         = "SNAPPY"
    "projection.enabled"          = "true"
    "projection.dt.type"          = "date"
    "projection.dt.format"        = "yyyy-MM-dd"
    "projection.dt.range"         = "2026-01-01,NOW"
    "projection.dt.interval"      = "1"
    "projection.dt.interval.unit" = "DAYS"
    "storage.location.template"   = "s3://${local.bucket_name}/silver/catalog_events/dt=$${dt}/"
  }

  partition_keys {
    name = "dt"
    type = "string"
  }

  storage_descriptor {
    location          = "s3://${local.bucket_name}/silver/catalog_events"
    input_format      = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format     = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"
    number_of_buckets = -1

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
      parameters = {
        "serialization.format" = "1"
      }
    }

    dynamic "columns" {
      for_each = local.silver_catalog_events_columns
      content {
        name = columns.value.name
        type = columns.value.type
      }
    }
  }

  lifecycle {
    ignore_changes = [parameters["transient_lastDdlTime"]]
  }
}

resource "aws_glue_catalog_table" "fact_listings" {
  name          = "fact_listings"
  database_name = aws_glue_catalog_database.olx_data.name
  table_type    = "EXTERNAL_TABLE"

  parameters = {
    "EXTERNAL"            = "TRUE"
    "parquet.compression" = "SNAPPY"
  }

  storage_descriptor {
    location      = "s3://${local.bucket_name}/gold/fact_listings"
    input_format  = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetInputFormat"
    output_format = "org.apache.hadoop.hive.ql.io.parquet.MapredParquetOutputFormat"

    ser_de_info {
      serialization_library = "org.apache.hadoop.hive.ql.io.parquet.serde.ParquetHiveSerDe"
      parameters = {
        "serialization.format" = "1"
      }
    }

    dynamic "columns" {
      for_each = local.fact_listings_columns
      content {
        name = columns.value.name
        type = columns.value.type
      }
    }
  }

  lifecycle {
    ignore_changes = [parameters["transient_lastDdlTime"]]
  }
}
