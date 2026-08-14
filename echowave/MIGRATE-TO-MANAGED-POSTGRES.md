# Moving the database off the container

One job: turn a 24-hour recovery point into minutes. Everything else here is in
service of doing that without losing a ledger row.

**Why this and not homegrown WAL archiving.** On the bundled Postgres, an
`archive_command` that fails stops WAL recycling and eventually wedges the
primary; `pg_receivewal` without a replication slot can silently gap, and with
one reintroduces the same disk risk. Managed Postgres gives you point-in-time
recovery as a setting, and answers who patches Postgres. See
`INFRASTRUCTURE.md §6.3`.

**What you are buying.** Not a product called PITR — managed Postgres, which
has it. On RDS the switch is `--backup-retention-period`: any value from 1 to
35 turns on automated backups *and* continuous WAL archiving, after which any
second inside the window is restorable.

**What this is not.** `--multi-az` is failover. It roughly doubles the instance
cost and does nothing for recovery point, because a bad `DELETE` replicates to
the standby instantly. If the budget forces a choice, take single-AZ with
7-day retention over Multi-AZ with none.

You do not have to do the whole of `INFRASTRUCTURE.md §6` to get this. Moving
only the database is an afternoon.

---

## 0. Decide these first

| | |
|---|---|
| Region | `ap-south-1`. `S3_REGION` is already Mumbai deliberately; putting recordings in Mumbai and the ledger in Virginia undoes that, and DPDP is the reason. |
| Instance class | From the connection budget in `INFRASTRUCTURE.md §6.1`. `db.r6g.large` for the modelled load. Below that, put PgBouncer in front — pool exhaustion presents as latency, and latency in a voice call gets blamed on the model. |
| Retention | 7 days. The number you can restore back to. |
| Multi-AZ | Optional, and orthogonal. Read the note above before deciding. |

Non-AWS providers are fine — Aurora, Supabase, Neon, DigitalOcean. Two hard
requirements: **pgvector** (migration `dc33eef8dabe` runs `CREATE EXTENSION
vector`), and a **Mumbai region**. Verify both before shortlisting; the
verifier below checks the first for you.

---

## 1. Provision

```bash
aws rds create-db-instance \
  --db-instance-identifier decibyl-prod \
  --engine postgres --engine-version 16 \
  --db-instance-class db.r6g.large \
  --allocated-storage 100 --storage-type gp3 \
  --storage-encrypted \
  --backup-retention-period 7 \
  --region ap-south-1
```

`--storage-encrypted` cannot be added in place later — it needs a snapshot
restore. This instance holds every phone number, recording reference and
invoice, so get it right now rather than after.

Then, as the master user, once:

```sql
CREATE EXTENSION vector;
```

RDS ships pgvector without enabling it. Skip this and the knowledge base
migration fails with a message that reads like a broken migration rather than a
missing extension.

**Check before you trust it:**

```bash
docker compose exec api python -m scripts.verify_managed_database \
  --database-url 'postgresql://decibyl:<pw>@decibyl-prod.xxxx.ap-south-1.rds.amazonaws.com:5432/decibyl' \
  --rds-instance decibyl-prod
```

Expect pgvector green, PITR green, and the schema check to warn that there is
no `alembic_version` yet — correct on an empty instance.

---

## 2. Rehearse before you touch production

```bash
docker compose exec -T api python -m scripts.fetch_latest_backup > backup.enc
PLATFORM_CREDENTIAL_SECRET=... ./scripts/rehearse_restore.sh backup.enc
```

This restores into a scratch database, reconciles the ledger, and prints how
long the restore took. Two reasons to do it now and not later: it is the first
honest measurement of your recovery time, and if last night's backup does not
restore cleanly you want to know that *before* you start a migration that
depends on a dump.

---

## 3. Cut over

The current box holds a live Razorpay ledger and issued receipt vouchers. This
is a data migration, not a config change. **Take the write path down first** —
a dump taken while calls are still being costed loses whatever is written
between the dump and the switch.

```bash
# 1. Stop writes. Nothing should be dialling or costing during this.
docker compose stop api worker

# 2. Final dump from the container.
docker compose exec -T postgres pg_dump -U postgres --format=custom postgres > cutover.dump

# 3. Restore into the managed instance.
pg_restore --no-owner --no-acl \
  --dbname 'postgresql://decibyl:<pw>@decibyl-prod.xxxx.ap-south-1.rds.amazonaws.com:5432/decibyl' \
  cutover.dump

# 4. Point the application at it. compose interpolates DATABASE_URL from .env,
#    so setting it here is enough — but see the trap below.
echo 'DATABASE_URL=postgresql+asyncpg://decibyl:<pw>@decibyl-prod.xxxx.ap-south-1.rds.amazonaws.com:5432/decibyl' >> .env

# 5. Bring the schema to head, then start.
docker compose run --rm api alembic upgrade head
./remote_up.sh
```

**The trap.** `docker-compose.yaml` reads
`DATABASE_URL: "${DATABASE_URL:-postgresql+asyncpg://...@postgres:5432/postgres}"`.
That interpolates from `.env` correctly *now*, but an earlier revision pinned it
under `environment:`, which wins over `env_file` — the stack came up healthy and
was still talking to the local container. Do not assume. Verify in step 4 below.

The bundled `postgres` service keeps starting even when nothing uses it. Stop
it with `--scale postgres=0` once you are confident, and **not before** — it is
your rollback.

---

## 4. Verify, in this order

```bash
# Is the application actually on the new database? Reads DATABASE_URL, so this
# answers the question rather than the one you think you configured.
docker compose exec api python -m scripts.verify_managed_database \
  --rds-instance decibyl-prod
```

Every check must be green, and one of them is the point of the whole exercise:

- **The ledger reconciles.** Every row records the running balance it produced,
  so each must equal the previous plus its own delta. A restore that dropped
  rows out of the middle leaves every row *count* plausible; this is what
  notices. If it fails, **do not cut over** — you are looking at a money
  history that is internally inconsistent.
- **PITR is enabled**, with a `LatestRestorableTime` inside the last few
  minutes. Retention of 0 means it is off however healthy the snapshot list
  looks.
- **pgvector is enabled**, and the schema is at this checkout's head.

Then the application-level checks:

```bash
docker compose exec api python -m scripts.verify_payment_round_trip
curl -s https://<host>/api/v1/admin/billing/readiness | jq '.blocking'
curl -s https://<host>/api/v1/privacy/readiness | jq '.checks[] | select(.key=="recovery_point")'
python scripts/e2e_smoke.py
```

Finally, tell the application the gap is closed:

```
DATABASE_PITR_ENABLED=true
```

`recovery_point` goes from `action_required` to `ready`. Do this **after** the
verifier confirms PITR, not before — the flag is a statement about the world,
and setting it while it is untrue is worse than leaving the finding open.

---

## 5. Rollback

Until you scale the `postgres` service to zero, rollback is:

```bash
# Remove the DATABASE_URL line from .env, then:
./remote_up.sh
```

The container still holds the pre-cutover data. Anything written to RDS after
the switch is lost by rolling back — which is why the write path goes down in
step 3 and stays down until step 4 is green.

Keep the old volume for a fortnight. `INFRASTRUCTURE.md §7` says the same about
the old box, for the same reason.

---

## What this does not close

`ACCEPTED_RECOVERY_POINT_HOURS` becomes irrelevant once PITR is on — that
variable exists to record a decision to live *without* it.

The hourly ledger snapshot (`LEDGER_SNAPSHOT_ENABLED`) becomes redundant but is
harmless: 25KB an hour, and a second copy of the money in a different place is
not a bad thing to keep. Switch it off if you prefer the tidiness.

The **backup blast radius** finding is separate and survives this migration:
RDS automated backups live in the same AWS account as everything else. That is
`BACKUP_MIRROR_*`, and it is a different afternoon.
