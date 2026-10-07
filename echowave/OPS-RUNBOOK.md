# Operations runbook

The one runbook for deploying and operating Decibyl (handoff sections 11, 14,
15 G-H, 34 and 35). Where `DEPLOY.md`, `INFRASTRUCTURE.md`, `STAGING.md` and
`docs/deployment/*.mdx` go deeper, this page links to them; where they
disagree with what the workflows actually do, **this page and the workflows
win**, and `python -m scripts.check_infra repo` fails or warns when the two
drift apart.

Contents: 1 what runs where; 2 what the docs said vs what the workflows do;
3 environment parity; 4 deploy, roll back, migrate; 5 routine operations;
6 provider keys; 7 observability and privacy; 8 health; 9 backups and the
restore drill; 10 capacity review; 11 cost stop; 12 Laya guardrails;
13 infrastructure checks; 14 the AWS target and what needs a human;
15 flags and rollback.

---

## 1. What runs where (today)

| Piece | Production | Staging |
| --- | --- | --- |
| Host | One EC2 box in `ap-south-1`, GitHub runner label `production` | The CI EC2 box, runner label `ci` (functional staging); a separate load-test box when one exists (`STAGING.md`) |
| Stack | `docker-compose.yaml`, profile `remote`: api (uvicorn + ARQ + ARI manager + campaign orchestrator, `DECIBYL_ROLE=all`), ui, sandbox, Postgres (pgvector), Redis, MinIO, nginx, coturn, FalkorDB | Same file, same profile |
| Deploy | `.github/workflows/deploy.yml`: push to `main` (non-docs) or manual dispatch; OIDC role -> SSM `send-command` -> `scripts/ci_deploy.sh` | `.github/workflows/deploy-staging.yml`: dispatch with a ref, or push to branch `staging`; runs `ci_deploy.sh` directly on the box, then `scripts/staging_check.py` |
| Images | **Built on the box** (`IMAGE_SOURCE=build`, the default in `ci_deploy.sh`) | Same |
| Config | `.env` on the box, overlaid from Parameter Store `/decibyl/prod/` on every deploy (`scripts/ops/config_sync.sh`) | `.env`, overlaid from `/decibyl/staging/` |
| Box operations | `.github/workflows/ops.yml` (status, logs, restart, migrate-status, config-*) over SSM | Shell on the CI box |
| Application operations | Staff console backed by `/api/v1/admin/ops/*` (this stream; flag `ops_console`) | Same |

## 2. What the docs said vs what the workflows do

Reconciled on 7 October 2026. Each line is now true in the doc it names.

| Doc said | What actually happens | Fixed in |
| --- | --- | --- |
| "Updating a running box": `git pull` then `remote_up.sh --build` | Routine updates are `deploy.yml` (push to `main`). The manual path is for a first box and break-glass only | `DEPLOY.md` |
| "Migrations do not run themselves on this path" | They do, twice: `start_services_docker.sh` runs `alembic upgrade head` at container start (roles `all` and `control`), and `ci_deploy.sh` runs it again after `up`. Only a `media`-role node skips them | `DEPLOY.md` |
| "Docs are a build artifact ... nothing builds it for you" | `ci_deploy.sh` builds `docs/dist` in a `node:22-alpine` container on every deploy (best effort; the old build stays on failure) | `DEPLOY.md` |
| `build-images.yml` header: images built "on every merge" | The push trigger was removed (commit 8e52017); it runs on manual dispatch only, and the box still builds its own images. A registry rollback (`IMAGE_TAG=<sha>`) needs a dispatched build of that SHA first | `build-images.yml` header, here |
| `INFRASTRUCTURE.md` 4: "until the start script is role-aware" | It is: `DECIBYL_ROLE=all|media|control` (`scripts/start_services_docker.sh`) | `INFRASTRUCTURE.md` |
| CI: tests "on every PR" | `api-tests.yml`, `ui-tests.yml` and the drift check run on PRs **into `main` only**. PRs into `claude/simpler-rail` get no CI; run the suites locally (`LAUNCH-PLAN.md`, "How to run and test") | here |

## 3. Environment parity

Staging exists so production only ever runs what staging has run. The two
must have **the same keys and different secrets**.

| Aspect | Production | Staging | Rule |
| --- | --- | --- | --- |
| Deploy script | `ci_deploy.sh` (via SSM) | `ci_deploy.sh` (direct) | Same script, read from the fetched commit (`git show FETCH_HEAD:...`) |
| Compose file and profile | `docker-compose.yaml`, `remote` | Same | Same |
| `ENVIRONMENT` | `production` | `staging` | Must differ: Sentry, PostHog and the ops console tag every signal with it |
| Config keys | `/decibyl/prod/` + `.env` | `/decibyl/staging/` + `.env` | Same key set; `check_infra parity` lists any key missing on staging |
| Secrets | Production | Fresh: `PLATFORM_CREDENTIAL_SECRET`, `OSS_JWT_SECRET`, `POSTGRES_PASSWORD`, `REDIS_PASSWORD`, `ANALYTICS_PSEUDONYM_KEY` | Never shared; `check_infra parity` compares digests and fails on a match without printing either value |
| Razorpay | Live keys | Test keys | Never live keys on staging |
| Model keys | Production accounts | Staging accounts with a monthly spend cap | |
| Flags | Follow staging | May run ahead | Production switches on only after staging passes |
| Post-deploy proof | Health + billing readiness | Health + `staging_check.py` (A/B accounts, privacy, model reply) | Suggestion: run `staging_check.py` read-only checks against production too |
| Database | Bundled Postgres container (target: RDS, `MIGRATE-TO-MANAGED-POSTGRES.md`) | Bundled | Parity of engine and extensions (pgvector) |

Check it:

```bash
# From a machine with read access to both Parameter Store paths:
python -m scripts.check_infra parity
# Or from two .env files copied off the boxes (never commit them):
python -m scripts.check_infra parity --prod-env prod.env --staging-env staging.env
```

## 4. Deploy, roll back, migrate

**Deploy production:** merge to `main` (or Actions -> Deploy -> ref). The
workflow assumes `AWS_DEPLOY_ROLE_ARN` over OIDC, sends `ci_deploy.sh` to the
box over SSM, and streams the result. `ci_deploy.sh`: fetch, check out the
exact SHA, sync config from Parameter Store, build (or pull with
`IMAGE_SOURCE=registry`), `up`, reload nginx, migrate, seed provider rates,
build docs, wait for `/api/v1/health`, print readiness, prune disk.

**Automatic rollback:** any failure before health passes checks out the
previous SHA and brings the stack back up -- unless the database is already
at a migration the previous commit does not have, in which case it stops and
says so (fix forward; never downgrade without reading the migration).

**Manual rollback:** redeploy the previous SHA with Actions -> Deploy -> ref.
With registry images: `IMAGE_TAG=<sha> ./remote_up.sh` (needs that SHA built
by `build-images.yml`).

**Voice drain before a deploy** (handoff 11, "Voice session ownership"): the
`workers.drain` / `workers.restart` ops commands start the SSM Automation
documents named in `OPS_SSM_DOCUMENTS`; until those documents exist, deploy
outside calling hours. A deploy during a call drops it.

**Record the deploy** (optional, feeds the console's "last deployment"):

```bash
curl -X POST https://<host>/api/v1/admin/ops/evidence -H "Authorization: Bearer $TOKEN" \
  -H 'content-type: application/json' \
  -d '{"kind":"deployment","outcome":"passed","summary":"<sha> deployed","link":"<run url>"}'
```

## 5. Routine operations

Two layers, both allowlisted, neither a shell:

* **Box level** -- `ops.yml`: status, redacted logs, restart, migrate-status,
  config-check / sync / rollback / import. Runbook:
  `docs/deployment/ops-without-ssh.mdx`.
* **Application level** -- typed commands (`api/services/ops/commands.py`,
  `POST /api/v1/admin/ops/commands`). Every request carries command, role,
  environment, target, reason, idempotency key; approval where required; a
  result. Accepted is **queued**, never "succeeded"; the ARQ worker runs it
  once. Every step is in `admin_action_log`.

| Command | Role | Approval | What it does |
| --- | --- | --- | --- |
| `queue.inspect`, `delivery.inspect`, `backups.status`, `deployments.status` | support | no | Read-only |
| `delivery.retry` | support | no | Retry a dead-lettered webhook (3 more attempts) |
| `agent.pause` / `agent.resume` | support | no | One agent stops / resumes taking calls; optional expiry |
| `routine.pause` / `routine.resume` | support | no | Disarm / re-arm a routine (re-arm only if test-run) |
| `provider.pause` | superadmin | yes | Stop offering one managed provider; optional expiry |
| `provider.resume` | superadmin | no | Offer it again |
| `flag.set` | superadmin | yes | Switch a feature; versioned |
| `flag.rollback` | superadmin | no | Restore the version before a `flag.set` |
| `laya.rollback` | support | no | Rules only; fast in an incident |
| `laya.restore` | superadmin | yes | Let Auto ask Laya again |
| `cost_stop.engage` | support | no | Refuse new billable work (platform or one workspace) |
| `cost_stop.release` | superadmin | yes | Allow it again |
| `workers.drain`, `workers.restart` | superadmin | yes | Start the approved SSM Automation runbook; `needs_setup` until mapped |

There is no command for IAM, networking, destructive recovery, arbitrary
SQL or shell, and none can be added from the UI.

## 6. Provider keys

Screen 42 reads `GET /api/v1/admin/ops/credentials`: provider, environment,
masked key, owner, validation time, last change, health. Rotation is the
handoff's lifecycle (`api/services/ops/credentials.py`), each step a
`POST /api/v1/admin/ops/credentials/rotations/{id}/{step}`:

1. **stage** -- sealed beside the live key; nothing uses it.
2. **validate** -- the vendor is asked. Rejected stops here; "could not ask"
   needs `force` to activate.
3. **activate** -- becomes the live key; the old ciphertext is kept.
4. **refresh** -- every worker re-reads tier availability; mirrored to
   Secrets Manager when `OPS_SECRET_BACKEND=aws_secrets_manager` (names under
   `OPS_SECRET_NAMESPACE` only). Calls read keys per call, never per frame.
5. **verify** -- the live key is checked again.
6. **revoke** -- the old ciphertext is destroyed; revert is now impossible.
   **Then revoke the old key on the vendor's dashboard** -- the response says so.

`revert` restores the previous key at any point before revoke. No route ever
returns a key; request bodies are dropped from Sentry; nothing logs them.
Rotating `PLATFORM_CREDENTIAL_SECRET` itself is a different job:
`docs/deployment/secrets-rotation.mdx`.

## 7. Observability and privacy

| System | Holds | Owner of the truth? |
| --- | --- | --- |
| Sentry | Exceptions, traces (api and worker, `api/observability/sentry.py`) | No |
| PostHog | Funnels and cohorts from **server events** (`api/services/ops/telemetry.py`) | No |
| CloudWatch / CloudTrail | Infrastructure metrics and alarms / AWS API activity | No |
| Application database | Tasks, approvals, usage ledger, audit, `ops_commands`, `ops_evidence` | **Yes** |

**Server events** use the `controls` stream's event catalogue
(`api/services/events/`): one `analytics_outbox`, written in the same
transaction as the change, delivered by `deliver_analytics_outbox` with the
event id as PostHog's uuid, under the catalogue's envelope (pseudonymous
user and workspace ids, typed properties, codes not free text). Ops adds
`ops_command_executed`, `cost_stop_engaged` and `cost_stop_released`
(domain `ops`) and the cost amounts on `usage_cost_recorded`, written when a
run is costed. Ops events need both `server_analytics` and `event_catalogue`
on; `api/services/ops/telemetry.py` redacts before the catalogue's check.

**Never in telemetry:** prompts, transcripts, audio, email bodies, keys, KYC
documents, card details. Enforced by `api/services/ops/redaction.py`:
properties are scalars only under non-content names (drops are listed in
`_redacted`), strings are scrubbed of secrets, emails and phone numbers,
errors become `reason_code`s. With `telemetry_redaction` on, the same scrub
runs over every log line and Sentry message, exception value, breadcrumb and
extra, frame locals are dropped, and the browser stops sending email and
name to PostHog.

**Replay:** PostHog is initialised with replay **off**. With `session_replay`
on, `SessionReplayGuard` starts it only on the allowlisted everyday screens
(`ui/src/lib/telemetry/privacy.ts`) with all inputs and text masked, and
stops it on any other route. The design's sensitive screens (23-27, 31-33,
35, 38, 42-44) are mapped to routes there and tested never to record.
Sentry runs no replay. Before switching replay on, look at a sample of
recordings: masking is a baseline, not proof.

**Retention** per store is the founder's to set before launch (handoff 35);
`PRIVACY.md` lists what the app enforces today.

## 8. Health: silence is never green

`GET /api/v1/admin/ops/health` (`api/services/ops/infra_health.py`):
database, Postgres pool occupancy, Redis (memory, clients), the ARQ queue
(depth and oldest due job), worker heartbeat, newest backup, last restore
drill, last capacity review, analytics backlog, monitoring configuration,
cost stop and Laya. Each is `ok`, `degraded`, `down`, `unknown` or
`not_configured`; overall is `ok` only when every core signal (database,
pool, Redis, queue, worker) was measured ok. A probe that hangs or throws is
`unknown`, never `ok`. CloudWatch alarms in `INSUFFICIENT_DATA` are reported
by `check_infra aws` as silence, not health.

## 9. Backups and the restore drill

Nightly encrypted `pg_dump` to object storage (`api/services/backup/`,
`BACKUP_ENABLED`). The drill, monthly and after any schema change, secret
rotation or storage move:

```bash
docker compose exec -T api python -m scripts.restore_drill
# a separate server for the scratch database (preferred for production):
docker compose exec -T api python -m scripts.restore_drill --scratch-url postgresql://...
```

It fetches the newest backup, restores it into a scratch database with
`rehearse_restore.sh` (decrypt to a temp dir, restore, check tables, the
migration head and that every ledger row reconciles, drop the scratch
database), and records an `ops_evidence` row (`restore_drill`) with the
restore time, ledger rows, drift rows and migration. The console's health
page shows it as overdue after 35 days and degraded if it failed. The time
recorded is data-restore time only, not a full RTO. With RDS, also check
`LatestRestorableTime` (`check_infra aws --rds-id ...`).

## 10. Capacity review

Quarterly and before any campaign that changes peak concurrency, against
staging (never production without `--allow-production`):

```bash
python -m scripts.capacity_review --base-url https://staging.decibyl.ai \
    --vcpus 8 --target-concurrency 40 --levels 1,10,25,50,100,200 --record
```

Ramps with `load_ramp.run_level`, stops at the knee, writes
`capacity-review/capacity-review.{md,json}`, passes only if the last good
level reaches the target, and with `--record` (and `OPS_EVIDENCE_TOKEN`)
posts the evidence. It measures HTTP requests in flight per vCPU; calls per
vCPU still need the voice soak in `INFRASTRUCTURE.md` section 8.

## 11. Cost stop

With `cost_stop` on and `COST_STOP_PLATFORM_HOURLY_PAISE` /
`COST_STOP_ORG_HOURLY_PAISE` set, `sweep_ops` compares the last hour's
provider cost (`call_cost_items.provider_cost_paise`) with the ceilings and
engages a stop when one is crossed. A stop refuses **new** runs at
`quota_service.authorize_workflow_run_start` with `cost_stopped`; running
work finishes. Automatic stops stay until a person releases them
(`cost_stop.release`, approved). Unset ceilings are reported as "not
monitored". Redis unreadable -> the check fails open and logs. When the
`controls` stream's operational quotas land, their gate calls
`cost_stop.check(organization_id)` and their counters can implement
`SpendSource`.

## 12. Laya guardrails

Auto routes each message by rules, with Laya in shadow (`LAYA_ROUTING`).

* **Rollback:** `laya_rollback` flag (or `laya.rollback` command): rules
  only, Laya never called, every worker within seconds.
* **Safe timeout:** with `laya_guardrails` on, each decision has a hard
  deadline (`LAYA_HARD_DEADLINE_MS`, default 500) and a circuit breaker
  (`LAYA_BREAKER_FAILURES` consecutive failures open it for
  `LAYA_BREAKER_COOLDOWN_SECONDS`). Every failure is an abstention; the rules
  decide; a reply is never lost to the classifier.
* **Shadow statistics:** counts only (agree, disagree by pair, abstained by
  reason, latency bucket, truncated) per day in Redis, shown at
  `GET /api/v1/admin/ops/laya`.
* **Evaluation:** `python -m scripts.eval_laya_routing [--record]` runs the
  rules and Laya over `evals/routing/laya_routing_labelled.jsonl` (normal,
  ambiguous and adversarial; en, hi, Hinglish, ta, te, code-mix) and reports
  accuracy, per-kind false positives and negatives, abstention, calibration
  error and p50/p95 latency by category and language. Rules baseline on the
  current set: 0.745. **Promotion gate** (exit 0 only when all hold): Laya
  with rules as fallback beats the rules by 2 points, abstains on at most 25%,
  p95 within the deadline, no category or language regresses, and at least
  300 reviewed samples. The set's labels were drafted from the KINDS
  definitions and need a human review pass.

## 13. Infrastructure checks

```bash
python -m scripts.check_infra repo       # the deploy path, vs this runbook
python -m scripts.check_infra parity     # production vs staging (section 3)
python -m scripts.check_infra aws --rds-id decibyl-prod --bucket decibyl-prod-recordings
```

`aws` (read-only): RDS PITR fresh, Multi-AZ, encryption; S3 public access
blocked; ECR repositories present; `decibyl*` CloudWatch alarms present and
not silent; the Secrets Manager namespace. Exit 1 on any failure.

## 14. The AWS target (handoff 11) and what needs a human

The target, not the present: ECS Fargate web/API behind an ALB with WAF;
separate ECS workers on ARQ/Redis; warm EC2 for voice and TURN with
session-to-worker ownership; RDS PostgreSQL + pgvector Multi-AZ; ElastiCache;
S3 with lifecycle; SES; isolated sandboxes; ECR, OIDC, Secrets Manager,
metrics, tracing and cost alarms. Order: inventory -> backups and a restore
test (section 9) -> isolated staging -> replay representative load
(section 10) -> migrate with rollback and voice drain. Sizing and the
interim EC2 plan: `INFRASTRUCTURE.md`. The database alone can move first:
`MIGRATE-TO-MANAGED-POSTGRES.md`.

Needs AWS access or a person (none of it is done by code in this repo):

* ECR push permissions on the deploy role, then re-enable the push trigger in
  `build-images.yml` and set `IMAGE_SOURCE=registry` (KAN-35).
* SSM Automation documents for `workers.drain` / `workers.restart`, mapped in
  `OPS_SSM_DOCUMENTS`, with `OPS_SSM_TARGET_INSTANCE_ID` and
  `ssm:StartAutomationExecution` / `GetAutomationExecution` for the API role
  on those documents only, and an approval step for designated principals.
* `secretsmanager:PutSecretValue` / `CreateSecret` on
  `arn:aws:secretsmanager:ap-south-1:<acct>:secret:decibyl/<env>/providers/*`
  if `OPS_SECRET_BACKEND=aws_secrets_manager`.
* CloudWatch alarms named `decibyl*` (worker heartbeat, queue age, RDS,
  disk) and a budget alarm.
* `ANALYTICS_PSEUDONYM_KEY` (fresh per environment), PostHog project key,
  cost-stop ceilings, and a first `restore_drill` and `capacity_review` on
  staging.
* Reviewing the Laya labelled set, and growing it past 300.

## 15. Flags and rollback

All off by default (`api/services/features.py`). Each is switched per
workspace or globally from the staff console (or `flag.set`), and turning one
off is its rollback.

| Flag | On means | Off (rollback) means |
| --- | --- | --- |
| `ops_console` | `/api/v1/admin/ops/*` answers; the command sweep runs | 404; nothing scheduled runs |
| `server_analytics` | Ops and cost events written to the controls outbox (with `event_catalogue` on) | None written; the outbox keeps what it has |
| `telemetry_redaction` | Deep scrub of logs and Sentry; no email/name to PostHog | Baseline Sentry scrub and phone masking only |
| `session_replay` | Masked replay on allowlisted screens | No replay anywhere |
| `laya_guardrails` | Hard deadline, breaker, shadow counters | `decision.choose` exactly as before |
| `laya_rollback` | Rules only, Laya never called | Auto as `LAYA_ROUTING` says |
| `cost_stop` | Ceilings evaluated; stops refuse new runs | Never refuses; engaged stops are ignored |

The migration `202610071400ops` (after `202610071500shell`) only adds three tables; downgrading drops
them and loses command history and evidence, so prefer switching flags off.
