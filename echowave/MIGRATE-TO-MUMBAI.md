# Moving the box from Virginia to Mumbai

`us-east-1` → `ap-south-1`, August 2026. Written during the move, so it is a
checklist rather than a history: everything below is a thing that has to be
true before the migration is finished, and most of them are true or false
silently.

**The reason for the move is data residency**, not latency — though Mumbai wins
that too. Call recordings and transcripts are conversations with people in
India, and under the DPDP Act the region holding the bucket is where that
personal data comes to rest. `KNOWN_ISSUES.md` #16 fixed the *default* so the
next deployment starts out right; a default does not move data that already
exists, and this document is the part that does.

---

## The thing to understand first

**A green deploy does not mean a finished migration.** `ci_deploy.sh` gates on
`GET /api/v1/health`, and that endpoint reads configuration and returns. It
never opens a database connection, never touches Redis, never asks S3
anything. It returns `ok` on:

- a box whose database is an empty schema `alembic upgrade head` just created;
- a box writing recordings to a bucket that is still in Virginia;
- a box whose `PLATFORM_CREDENTIAL_SECRET` was regenerated, so every stored
  provider key is now undecryptable ciphertext;
- a box nothing points at, while the old instance quietly keeps taking calls.

Each of those is a working deployment by every signal the pipeline has. They
are what `scripts/verify_region_migration.py` exists to catch.

---

## 1. Before you stop the old box — take the baseline

This is the only step that cannot be done later, and it takes one command. Run
it on the **old** instance, while it still holds the truth:

```bash
cd /home/ubuntu/echowave-redesign/echowave
docker compose exec -T api python -m scripts.verify_region_migration \
    --emit-baseline > /tmp/old-box.json
```

**If that answers `service "api" is not running`**, the stack is down — which
is the normal state of a box you are migrating away from, and exactly when the
baseline has to be taken. Use the postgres-only path instead:

```bash
./scripts/emit_migration_baseline.sh > /tmp/old-box.json
```

It starts postgres if it has to, and produces the same JSON. It deliberately
does **not** start the api container: the arq worker, the scheduled backup and
the campaign orchestrator all run inside it under supervisord, so waking it on
a box you are decommissioning means backups taken from a database that is now
a fork, and campaigns dialling real numbers out of it.

That file is what "completely migrated" means numerically — row counts per
table, the schema revision, and the timestamp of the newest call. Copy it off
the box. Without it, every data check downgrades from *nothing was left
behind* to *there is something here*, and a restore from a month-old backup
passes the second one.

Keep the old instance **stopped, not terminated**, until everything below is
green. Stopped keeps the EBS volumes; terminated does not.

## 2. What has to be carried across by hand

Four categories, in the order they hurt:

| | Moves with | Does **not** move on its own |
|---|---|---|
| Schema | `alembic upgrade head` on every deploy | — |
| Data | — | Postgres volume: dump on the old box, restore on the new |
| Objects | — | MinIO volume or the S3 bucket's contents |
| Config | — | `.env`, in full. `ci_deploy.sh` never writes it, by design |

**`.env` is the one people rebuild from the template and regret.** Copy the
file, do not re-derive it. Three values in it cannot be regenerated:

- **`PLATFORM_CREDENTIAL_SECRET`** — every provider key, carrier credential and
  Google OAuth token in the database is Fernet-encrypted under it. A new value
  does not fail loudly: the UI still lists every key with its masked last four
  intact, because the last four are stored in plaintext beside the ciphertext.
  What fails is the vendor call, later, looking like a rotated vendor key.
- **`SECRET_KEY` / session signing** — a new value logs out every user at once.
- **`RAZORPAY_*`** — live keys, and the webhook secret has to match what
  Razorpay is configured to sign with.

## 3. Bucket contents, not bucket names

A restored database brings across every recording *URL* and not one recording.
If `ENABLE_AWS_S3=true`, the bucket is a separate migration:

**A bucket cannot change region.** There is no move operation. Create a new
bucket in `ap-south-1`, copy the objects, repoint `S3_BUCKET`:

```bash
aws s3 mb s3://<new-bucket> --region ap-south-1
aws s3 sync s3://<old-bucket> s3://<new-bucket> --source-region us-east-1 --region ap-south-1
```

Do the same for `KYC_BUCKET` — those are identity documents, and they are the
files with the tighter retention rule, not the looser one.

`BACKUP_MIRROR_BUCKET` should end up in a *different* region from the primary
on purpose. A mirror beside the thing it mirrors survives a hardware failure
and nothing larger.

If storage is MinIO (`ENABLE_AWS_S3=false`), the objects are on the box's own
disk in the `minio-data` volume, and they move only if that volume was copied.

## 4. The deploy pipeline is regional

SSM is a regional API. An instance id resolves only in the region that holds
it, so **four things carry the region and all four must agree**:

1. the `AWS_REGION` repository variable → `ap-south-1`;
2. the `EC2_INSTANCE_ID` secret → the new instance's id;
3. the instance ARN inside the `DecibylGitHubDeploy` inline policy, which pins
   `ssm:SendCommand` to one instance. The region is wildcarded there; the
   instance id is not;
4. the `DecibylEC2SSM` instance profile, **attached to the new instance**. IAM
   roles are global, but the association is per-instance and does not follow a
   snapshot or an AMI copy.

Any one of them left behind fails the deploy at `send-command` with
`InvalidInstanceId` — an error that names the instance and says nothing about
the region, which is the part that is wrong.

Confirm the new box has checked in before trusting any of it:

```bash
aws ssm describe-instance-information --region ap-south-1 \
  --query "InstanceInformationList[].{Id:InstanceId,Ping:PingStatus}" --output table
```

Full setup, including the trust policy, is `docs/DEPLOY-GITHUB-ACTIONS.md`.

## 5. Everything that points at the old IP

The instance changed region, so it changed public IP. DNS is the obvious one
and not the only one:

- **A records** for the apex, `app.`, `api.`, `docs.` — and the TTL you set
  before the move, not after.
- **`PUBLIC_BASE_URL`, `PUBLIC_HOST`, `TURN_HOST`** in `.env`. On the sslip.io
  path the hostname *is* the IP (`203-0-113-10.sslip.io`), so a new IP means a
  new hostname and a new certificate.
- **TLS certificates** — reissued for the new hostname, or copied with the box.
  The browser will not hand over a microphone without one, so a certificate
  problem presents as "the agent cannot hear anyone".
- **Razorpay webhooks** — Settings → Webhooks →
  `https://<host>/api/v1/billing/razorpay/webhook`.
- **Carrier inbound and status callbacks**, per carrier.
- **Google OAuth redirect URIs**, if the hostname changed.
- **Any vendor allowlist naming the old box's egress IP.** This one has no
  local symptom at all: it fails at the vendor, for one integration, whenever
  it is next used.

## 6. Prove it

On the new box, with the baseline from step 1:

```bash
cd /home/ubuntu/echowave-redesign/echowave
docker compose exec -T api python -m scripts.verify_region_migration \
    --baseline /tmp/old-box.json
```

It exits non-zero until the migration is real. What it checks, and why each one
is on the list rather than assumed:

| Check | The failure it catches |
|---|---|
| Instance region and id | Running the migration against the box you meant to leave |
| `S3_REGION`, and `get_bucket_location` on every bucket | The variable says Mumbai and the bucket is still Virginia — buckets do not move |
| Row counts against the baseline | A fresh schema, or a partial restore, both of which serve traffic happily |
| Newest `workflow_runs` timestamp | A restore from a backup older than the last calls taken |
| Alembic revision vs. the checkout | A dump taken on an older schema, missing columns the code writes to |
| Every stored credential decrypts, verified against `key_last_four` | A regenerated `PLATFORM_CREDENTIAL_SECRET` |
| The newest recordings actually exist in the bucket | Rows migrated, objects did not |
| `PUBLIC_BASE_URL` and `TURN_HOST` resolve to *this* box's IP | DNS still pointing at Virginia while you test Mumbai |
| Redis reachable | The arq worker, scheduled backups and queued runs, all silently stopped |

It also warns about the things a script cannot decide — whether the old
instance is stopped, whether the carriers were re-pointed — because leaving
them off the list would read as their having passed.

## 7. Then test, in this order

Cheapest first, and each step is a precondition for the next:

1. **`curl -fsS https://<host>/api/v1/health | jq`** — liveness, plus it echoes
   `deployment_mode`, `auth_provider` and `turn_enabled`, which is a quick read
   on whether `.env` came across whole.
2. **`scripts/verify_region_migration.py`** — as above. Do not proceed past a
   red one.
3. **`docker compose exec -T api python -m scripts.fetch_latest_backup > b.enc`**
   then **`./scripts/rehearse_restore.sh b.enc`** — proves backups are being
   taken *on this box* and are restorable under the credential secret it has
   now. An untested backup on a new box is a hypothesis about a machine that
   did not exist last week.
4. **Log in as a real existing user.** Not a new signup — an account from
   before the move. It proves the data restored, the session secret survived,
   and the org scoping resolves.
5. **Play a recording from before the move**, through the UI. This is the
   single best end-to-end check that objects and presigned URLs both moved: it
   crosses the database, the bucket, the region and the signature version in
   one click.
6. **`python scripts/e2e_smoke.py`** — signup → agreements → billing profile →
   top-up → ledger → invoice, against the real HTTP API. Note it targets
   `127.0.0.1:8100` by default; run it on the box, or point `BASE` at the new
   host.
7. **One outbound test call, to a number you hold.** The first thing that
   exercises the carrier credentials, TURN, and the recording upload path
   together. Check afterwards that the recording landed in the new bucket and
   that `workflow_runs.cost_info` was populated — billing writes are the
   quietest thing to lose.
8. **One inbound call**, if inbound is configured. It is the only check on the
   carrier's callback URL actually pointing here.
9. **A ₹1 Razorpay top-up**, in live mode. The webhook is the part that moved,
   and a webhook that 404s looks exactly like a customer's payment failing.

## 8. Only then

Snapshot the old instance's volumes, then terminate it. Keep the snapshot for
the retention period you would want if something surfaces in three weeks —
which is when the thing nobody checked usually surfaces.
