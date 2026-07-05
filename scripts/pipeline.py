"""Cloud pipeline runner: bronze upload + silver/gold Athena transforms.

Replaces the manual runbook steps (aws s3 cp / console-pasted SQL) with one
command per stage:

    uv run python scripts/pipeline.py upload                      # bronze: runs/ -> s3://.../raw/
    uv run python scripts/pipeline.py silver --dt 2026-07-05      # rebuild one dt of silver
    uv run python scripts/pipeline.py gold                        # full-rebuild gold
    uv run python scripts/pipeline.py verify                      # health checks

The transform SQL lives in infra/silver/ and infra/gold/ (pure SQL, no
comments); this script only fills the __DT__/__REGION__ tokens and drives
Athena. The bucket is derived from the caller's AWS account id
(olx-data-<account>), so nothing here breaks if the account changes; override
any value with --bucket/--workgroup/--database/--region.

Profiles: upload runs as `olx-scraper` (write-only); transforms/verify run as
`olx-pipeline` (least-privilege user managed in infra/terraform/iam.tf).
"""
import argparse
import sys
import time
from pathlib import Path

import boto3

ROOT = Path(__file__).resolve().parent.parent
SILVER_SQL = ROOT / 'infra' / 'silver' / 'catalog_events.sql'
GOLD_SQL = ROOT / 'infra' / 'gold' / 'fact_listings.sql'

DEFAULT_WORKGROUP = 'olx_data'
DEFAULT_DATABASE = 'olx_data'
DEFAULT_REGION = 'us-east-1'

POLL_INITIAL_S = 2
POLL_MAX_S = 5
POLL_TIMEOUT_S = 15 * 60


def _session(args):
    return boto3.Session(profile_name=args.profile, region_name=args.region)


def _bucket(args, session):
    if args.bucket:
        return args.bucket
    account = session.client('sts').get_caller_identity()['Account']
    return f'olx-data-{account}'


# --------------------------------------------------------------- athena ----

def run_query(session, workgroup, sql, label):
    athena = session.client('athena')
    exec_id = athena.start_query_execution(
        QueryString=sql, WorkGroup=workgroup
    )['QueryExecutionId']
    print(f"  {label}: {exec_id} ...", end='', flush=True)

    delay = POLL_INITIAL_S
    waited = 0
    while True:
        time.sleep(delay)
        waited += delay
        delay = min(delay * 1.5, POLL_MAX_S)
        state = athena.get_query_execution(QueryExecutionId=exec_id)
        status = state['QueryExecution']['Status']
        if status['State'] in ('SUCCEEDED', 'FAILED', 'CANCELLED'):
            break
        if waited > POLL_TIMEOUT_S:
            athena.stop_query_execution(QueryExecutionId=exec_id)
            print(" TIMEOUT")
            sys.exit(f"Query {exec_id} exceeded {POLL_TIMEOUT_S}s and was stopped.")

    if status['State'] != 'SUCCEEDED':
        print(f" {status['State']}")
        reason = status.get('StateChangeReason', '(no reason given)')
        sys.exit(f"Query {exec_id} {status['State']}: {reason}")

    stats = state['QueryExecution'].get('Statistics', {})
    scanned = stats.get('DataScannedInBytes', 0)
    print(f" SUCCEEDED ({scanned / 1024:.1f} KiB scanned)")
    return exec_id


def query_rows(session, workgroup, sql, label):
    """Run a SELECT and return its rows as lists of strings (header included)."""
    exec_id = run_query(session, workgroup, sql, label)
    results = session.client('athena').get_query_results(QueryExecutionId=exec_id)
    return [
        [col.get('VarCharValue', '') for col in row['Data']]
        for row in results['ResultSet']['Rows']
    ]


# ------------------------------------------------------------------- s3 ----

def delete_prefix(session, bucket, prefix, dry_run, assume_yes):
    s3 = session.client('s3')
    keys = []
    paginator = s3.get_paginator('list_objects_v2')
    for page in paginator.paginate(Bucket=bucket, Prefix=prefix):
        keys.extend(obj['Key'] for obj in page.get('Contents', []))

    if not keys:
        print(f"  s3://{bucket}/{prefix}: already empty")
        return

    print(f"  s3://{bucket}/{prefix}: {len(keys)} object(s) to delete")
    if dry_run:
        for k in keys[:10]:
            print(f"    (dry-run) {k}")
        if len(keys) > 10:
            print(f"    (dry-run) ... and {len(keys) - 10} more")
        return

    if not assume_yes:
        answer = input(f"  Delete {len(keys)} object(s) under {prefix}? [y/N] ")
        if answer.strip().lower() != 'y':
            sys.exit("Aborted.")

    for i in range(0, len(keys), 1000):
        batch = [{'Key': k} for k in keys[i:i + 1000]]
        s3.delete_objects(Bucket=bucket, Delete={'Objects': batch, 'Quiet': True})
    print(f"  deleted {len(keys)} object(s)")


# --------------------------------------------------------------- upload ----

def cmd_upload(args):
    session = _session(args)
    bucket = _bucket(args, session)
    s3 = session.client('s3')

    runs_dir = Path(args.runs_dir)
    if not runs_dir.is_dir():
        sys.exit(f"No runs directory at {runs_dir}")

    files = sorted(
        p for p in runs_dir.rglob('*')
        if p.is_file() and p.suffix != '.tmp'
    )
    if not files:
        sys.exit(f"Nothing to upload under {runs_dir}")

    print(f"Uploading {len(files)} file(s) from {runs_dir}/ to s3://{bucket}/raw/")
    for path in files:
        key = f"raw/{path.relative_to(runs_dir).as_posix()}"
        if args.dry_run:
            print(f"  (dry-run) {path} -> {key}")
            continue
        s3.upload_file(str(path), bucket, key)
        print(f"  {key}")
    print("Upload complete." if not args.dry_run else "Dry run complete.")


# --------------------------------------------------------------- silver ----

def _regions_for_dt(args, session, bucket):
    if args.region_slug:
        return args.region_slug

    manifest_dir = Path(args.runs_dir) / 'manifests' / 'spider=catalog' / f'dt={args.dt}'
    if manifest_dir.is_dir():
        regions = sorted(
            p.name.split('=', 1)[1] for p in manifest_dir.iterdir()
            if p.is_dir() and p.name.startswith('region=')
        )
        if regions:
            return regions

    # No local manifests for this dt (e.g. different machine): list the raw
    # partition on S3 instead. olx-pipeline has ListBucket.
    s3 = session.client('s3')
    prefix = f'raw/spider=catalog/dt={args.dt}/'
    result = s3.list_objects_v2(Bucket=bucket, Prefix=prefix, Delimiter='/')
    regions = sorted(
        cp['Prefix'][len(prefix):].rstrip('/').split('=', 1)[1]
        for cp in result.get('CommonPrefixes', [])
    )
    if not regions:
        sys.exit(f"No regions found for dt={args.dt} (locally or under s3://{bucket}/{prefix})")
    return regions


def cmd_silver(args):
    session = _session(args)
    bucket = _bucket(args, session)
    regions = _regions_for_dt(args, session, bucket)
    template = SILVER_SQL.read_text(encoding='utf-8')

    print(f"Silver rebuild for dt={args.dt}, region(s): {', '.join(regions)}")
    delete_prefix(session, bucket, f'silver/catalog_events/dt={args.dt}/',
                  args.dry_run, args.yes)

    for region in regions:
        sql = template.replace('__DT__', args.dt).replace('__REGION__', region)
        if args.dry_run:
            print(f"  (dry-run) INSERT for region={region}")
            continue
        run_query(session, args.workgroup, sql, f'silver dt={args.dt} region={region}')
    print("Silver complete." if not args.dry_run else "Dry run complete.")


# ----------------------------------------------------------------- gold ----

def cmd_gold(args):
    session = _session(args)
    bucket = _bucket(args, session)
    sql = GOLD_SQL.read_text(encoding='utf-8')

    print("Gold full rebuild (fact_listings)")
    delete_prefix(session, bucket, 'gold/fact_listings/', args.dry_run, args.yes)

    if args.dry_run:
        print("  (dry-run) gold INSERT")
        print("Dry run complete.")
        return
    run_query(session, args.workgroup, sql, 'gold fact_listings')
    print("Gold complete.")


# --------------------------------------------------------------- verify ----

def cmd_verify(args):
    session = _session(args)
    db = args.database
    failures = 0

    rows = query_rows(session, args.workgroup, 'SELECT 1', 'smoke')
    print(f"  smoke SELECT 1 -> {rows[1][0]}")

    rows = query_rows(
        session, args.workgroup,
        f'SELECT COUNT(*), SUM(price_brl), AVG(size_m2) FROM {db}.silver_catalog_events',
        'silver typing',
    )
    count, total, avg = rows[1]
    print(f"  silver: rows={count} sum(price_brl)={total} avg(size_m2)={avg}")
    if count == '0':
        print("  WARNING: silver is empty")

    rows = query_rows(
        session, args.workgroup,
        f'SELECT uid, COUNT(*) AS c FROM {db}.fact_listings GROUP BY uid HAVING COUNT(*) > 1 LIMIT 10',
        'gold grain',
    )
    dups = rows[1:]
    if dups:
        failures += 1
        print(f"  FAIL: {len(dups)} uid(s) with duplicate rows in fact_listings:")
        for uid, c in dups:
            print(f"    uid={uid} rows={c}")
    else:
        rows = query_rows(session, args.workgroup,
                          f'SELECT COUNT(*) FROM {db}.fact_listings', 'gold count')
        print(f"  gold: rows={rows[1][0]}, one row per uid OK")

    if failures:
        sys.exit(f"{failures} check(s) failed")
    print("All checks passed.")


# ----------------------------------------------------------------- main ----

def _add_common(p, default_profile):
    p.add_argument('--profile', default=default_profile,
                   help=f'AWS profile (default: {default_profile})')
    p.add_argument('--region', default=DEFAULT_REGION,
                   help=f'AWS region (default: {DEFAULT_REGION})')
    p.add_argument('--bucket', default=None,
                   help='Bucket override (default: olx-data-<account-id>)')
    p.add_argument('--workgroup', default=DEFAULT_WORKGROUP,
                   help=f'Athena workgroup (default: {DEFAULT_WORKGROUP})')
    p.add_argument('--database', default=DEFAULT_DATABASE,
                   help=f'Glue database (default: {DEFAULT_DATABASE})')
    p.add_argument('--dry-run', action='store_true',
                   help='Print what would happen without touching AWS data')


def main():
    parser = argparse.ArgumentParser(description=__doc__.split('\n')[0])
    sub = parser.add_subparsers(dest='command', required=True)

    p = sub.add_parser('upload', help='Upload local runs/ tree to s3://<bucket>/raw/')
    _add_common(p, 'olx-scraper')
    p.add_argument('--runs-dir', default='runs', help='Local runs directory (default: runs)')
    p.set_defaults(func=cmd_upload)

    p = sub.add_parser('silver', help='Rebuild one dt partition of silver_catalog_events')
    _add_common(p, 'olx-pipeline')
    p.add_argument('--dt', required=True, help='Partition date, YYYY-MM-DD')
    p.add_argument('--region-slug', action='append', metavar='SLUG',
                   help='Region slug (repeatable; default: derive from manifests, then S3)')
    p.add_argument('--runs-dir', default='runs', help='Local runs directory (default: runs)')
    p.add_argument('--yes', action='store_true', help='Skip the delete confirmation prompt')
    p.set_defaults(func=cmd_silver)

    p = sub.add_parser('gold', help='Full-rebuild fact_listings from silver')
    _add_common(p, 'olx-pipeline')
    p.add_argument('--yes', action='store_true', help='Skip the delete confirmation prompt')
    p.set_defaults(func=cmd_gold)

    p = sub.add_parser('verify', help='Smoke query + silver typing + gold grain checks')
    _add_common(p, 'olx-pipeline')
    p.set_defaults(func=cmd_verify)

    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
