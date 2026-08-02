# Hosting Decibyl in ap-south-1

How the platform should be deployed to carry the Telangana campaign, and what
has to change to get there. Every capacity and cost figure comes from
`scripts/infra_sizing.py`, which derives them from the same volume model as the
quote — so if call length or connect rate moves, the fleet moves with it.

```bash
set -a && source api/.env && set +a && python -m scripts.infra_sizing
```

---

## 0. The verdict

**Two c7i.2xlarge media instances, one control node, RDS Multi-AZ and
ElastiCache, in `ap-south-1`. About ₹63,000 for the campaign against ₹95,000
carried in the bid.**

Three things qualify that:

1. **The load is smaller than it sounds.** 80,000 farmers is a big list and a
   small server. Peak is around 40 concurrent conversations. The fleet is sized
   by the busiest hour, not by the headline number.
2. **The compose stack is single-box by construction.** Three processes in it
   are singletons that nothing enforces. Running the same container on three
   nodes does not scale the platform — it triples campaign dispatch. §4.
3. **5 concurrent calls per vCPU is an estimate, not a measurement.** It sets
   the entire fleet size and it has never been tested. §8 is the test.

---

## 1. What is deployed today

One EC2 instance running `docker-compose.yaml`. Postgres, Redis, MinIO, nginx,
coturn, the UI and the API are sibling containers on one host with one set of
local disks.

Inside the `api` container, `scripts/start_services_docker.sh` starts **four
different services in one process tree**:

| Process | Count | Role |
|---|---|---|
| `alembic upgrade head` | once, at boot | schema migration |
| `ari_manager` | 1 | Asterisk event listener |
| `campaign_orchestrator` | 1 | batch scheduling, retries, completion |
| `uvicorn` | `FASTAPI_WORKERS` | HTTP **and every live media pipeline** |
| `arq` | `ARQ_WORKERS` | post-call costing, reports, backups |

The fourth row is the one that governs sizing. `run_pipeline_telephony` is
invoked from inside the provider's WebSocket handler
(`providers/plivo/provider.py:346` and its equivalents), so **the API workers
are the media workers**. A concurrent call is a WebSocket plus an asyncio task
doing VAD, resampling and codec work in the same process that serves the
dashboard. There is no separate media tier to scale independently.

nginx balances across `FASTAPI_WORKERS` uvicorn processes on ports
8000..800N with `least_conn` — deliberately, because uvicorn's own
`--workers` pins a long-lived WebSocket to whichever worker accepted it. That
works well and it is **per host**: the upstream list is local ports, so it
balances processes, never machines.

---

## 2. The load it has to carry

```
conversation minutes                83,333
average concurrent calls              19.8
peak concurrent (x2.0)                39.7
average SIP channels                  36.8
peak SIP channels                     73.6   <- order this many from the carrier
```

Two numbers deserve attention.

**Peak, not average.** Average concurrency is conversation-seconds over
window-seconds. Calls are not flat across 09:00–19:00 — mid-morning and early
evening carry far more answered calls than the 13:00 lull. The 2.0 peak factor
is a working assumption; the pilot will produce the real hourly profile and it
should replace the constant.

**SIP channels ≠ concurrent conversations.** An unanswered call holds a channel
for ~30 seconds of ringing while contributing no conversation. At a 27.9%
connect rate most attempts are unanswered, which is why channels run near
double conversations. **Order ~75 channels from the carrier, not ~40.** Getting
this wrong shows up as `503` from the carrier during the busiest hour, which
looks like a platform fault and is not one.

---

## 3. The target architecture

```
                    Route 53  ──►  ACM cert
                         │
                  ┌──────▼───────┐
                  │     ALB      │   HTTPS + WSS, idle timeout 300s
                  └──┬────────┬──┘
                     │        │
        ┌────────────▼──┐  ┌──▼────────────┐        ap-south-1a / 1b
        │  media-1      │  │  media-2      │   c7i.2xlarge, in-window only
        │  uvicorn x8   │  │  uvicorn x8   │   ~24 concurrent calls each
        └────────┬──────┘  └──────┬────────┘
                 │                │
        ┌────────▼────────────────▼────────┐
        │          control node             │   m7i.large, always on
        │  orchestrator · ari-manager · arq │   SINGLETONS — exactly one
        └────────┬────────────────┬────────┘
                 │                │
     ┌───────────▼──────┐  ┌──────▼──────────────┐
     │ RDS PostgreSQL 16│  │ ElastiCache Redis   │
     │ + pgvector       │  │ concurrency, ARQ,   │
     │ Multi-AZ, KMS    │  │ worker sync bus     │
     └──────────────────┘  └─────────────────────┘
                 │
          ┌──────▼──────┐
          │  S3 SSE-KMS │  recordings, transcripts, backups
          └─────────────┘
```

**Why the control node is separate and singular.** §4.

**Why not Kubernetes.** `deploy/helm/decibyl/` is a complete chart — it already
pins the orchestrator and ARI manager to `replicas: 1` with `strategy:
Recreate`, runs migrations as a `Job` rather than per-pod, and has HPA on the
web tier and an `examples/values-aws.yaml` for EKS. It is the *correct* shape
and it is the right answer the moment this platform runs continuously. For a
seven-day campaign it is the wrong trade: an EKS control plane, a load-balancer
controller, IRSA and a Gateway API controller are four new things to be on call
for, none of which we have operated. Take the boring path for the tender and
move to the chart when the second campaign justifies it.

---

## 4. Three things that break if you just add nodes

All three produce plausible behaviour rather than an error, which is the
failure mode this codebase keeps producing and the reason to write them down.

### 4.1 The campaign orchestrator is a singleton nothing enforces

`campaign_orchestrator.py` subscribes to a Redis **pub/sub** channel
(`_listen_for_events`, line 84) and dedupes with an **in-memory dict**
(`_processing_locks`, line 48; checked at line 339).

Pub/sub fans out to every subscriber. The in-memory lock only dedupes within
one process. Run the container on three nodes and all three receive every
`BatchCompletedEvent`, each consults its own empty lock, and each schedules the
next batch. **You get three times the dial attempts.** Nothing errors; the
campaign simply burns its list at 3× the intended rate, and the first evidence
is the carrier bill or a complaint about repeat calls.

Fix for the tender: run it on exactly one node. Fix for the product: a Redis
`SET NX EX` lease around `_schedule_next_batch`, or the Helm chart's
`replicas: 1`.

### 4.2 The ARI manager is the same shape

It opens a WebSocket to every organisation's Asterisk instance and handles
`StasisStart`. N copies means N listeners racing to answer the same inbound
call. Same containment: one node.

### 4.3 Every container migrates the database at boot

`start_services_docker.sh` runs `alembic upgrade head` unconditionally before
starting anything. Three nodes booting together run three concurrent
migrations against one database.

Fix for the tender: bring the control node up first, let it migrate, then start
the media nodes. Fix for the product: the chart's `migrate-Job`.

**Consequence for the build:** the media nodes must run *without* the
orchestrator and ARI manager. Until the start script is role-aware, that is a
one-line override on the media hosts — set `ARQ_WORKERS=0` and comment the two
`start` lines, or run them from a compose override with a different `command`.
This is the piece of §6 most likely to be got wrong.

---

## 5. What already scales correctly

Worth stating, because it is why this is a deployment problem and not a rewrite.

- **Concurrency accounting is already distributed.** `campaign/rate_limiter.py`
  enforces the org and per-campaign caps with a Lua script over Redis sorted
  sets — atomic, and correct across any number of hosts. This is the single
  most important thing to have got right, and it was.
- **From-number pool** with acquire/release, so 179,000 attempts spread across
  numbers instead of burning one CLI's reputation.
- **Circuit breaker** pausing a campaign on a failure-rate spike.
- **The DB pool is sized deliberately** — 20 + 20 overflow, with a 10-second
  timeout chosen so a call that cannot get a connection fails fast instead of
  leaving a caller in silence.
- **API and ARQ workers are stateless.** Instance count is the only lever.
- **S3 is already a supported backend** (`ENABLE_AWS_S3`), so MinIO does not
  have to follow us to production.

---

## 6. The build

### 6.1 Connection budget first

This determines the RDS instance, so settle it before provisioning.

```
media nodes:   2 hosts x 8 uvicorn x (20 pool + 20 overflow)  = 640
control node:  1 orchestrator + 1 ari + 2 arq, x 40           = 160
                                                        total = 800
```

`db.r6g.large` (16 GB) permits roughly 1,600 connections — comfortable.
`db.t4g.medium` does not. If you drop below `r6g.large`, put PgBouncer in front
in transaction mode; without it, pool exhaustion presents as **latency**, and
latency in a voice call gets blamed on the model.

Set `POSTGRES_MAX_CONNECTIONS` only for the bundled container; on RDS this is
the parameter group.

### 6.2 Network

```bash
REGION=ap-south-1

# VPC across two AZs. Public subnets for the ALB and NAT, private for
# everything that holds farmer data.
aws ec2 create-vpc --cidr-block 10.20.0.0/16 --region $REGION
# subnets: 10.20.0.0/24 + 10.20.1.0/24 public   (ap-south-1a / 1b)
#          10.20.10.0/24 + 10.20.11.0/24 private
```

Security groups, tightest first:

| Group | Inbound |
|---|---|
| `alb` | 443 from `0.0.0.0/0` |
| `media` | 8000-8007 from `alb` only |
| `control` | 22 from your IP; 8000 from `alb` |
| `rds` | 5432 from `media` + `control` |
| `redis` | 6379 from `media` + `control` |

The media nodes must **not** take traffic directly. The webhook signature
checks depend on the request URL matching what the carrier signed, which is why
`FORWARDED_ALLOW_IPS: "*"` is set in compose — that is safe behind an ALB and
is not safe on a naked public port.

### 6.3 Data tier

```bash
aws rds create-db-instance \
  --db-instance-identifier decibyl-prod \
  --engine postgres --engine-version 16 \
  --db-instance-class db.r6g.large \
  --allocated-storage 100 --storage-type gp3 \
  --multi-az --storage-encrypted \
  --backup-retention-period 7 \
  --region ap-south-1
```

`CREATE EXTENSION vector;` after it comes up — RDS supports pgvector but does
not enable it, and the migration that needs it fails with a message that reads
like a broken migration rather than a missing extension.

ElastiCache Redis, `cache.t4g.medium`. Single-AZ is modelled; Multi-AZ costs
₹5,500 more and is worth taking if the budget holds — Redis holds the
concurrency counters and the ARQ queue, so losing it mid-campaign stalls
dispatch.

S3 bucket with SSE-KMS, **public access blocked**, and a lifecycle rule that
matches the retention window already enforced in the application.

### 6.4 The instances

Ubuntu 24.04, `ap-south-1`. The supported path is `sudo ./setup_remote.sh`
followed by `./remote_up.sh` — the first provisions Docker, writes `.env`,
renders nginx and installs the certificate; the second is the startup
entrypoint and runs `docker compose config -q` as a preflight. Do not
`docker compose up` directly; the nginx and coturn configs are rendered at
runtime by the `decibyl-init` service and a bare `up` comes up misconfigured.

`.env` additions that make the box use the managed data tier:

```
DATABASE_URL=postgresql+asyncpg://decibyl:<pw>@decibyl-prod.xxxx.ap-south-1.rds.amazonaws.com:5432/decibyl
REDIS_URL=rediss://:<token>@decibyl-cache.xxxx.ap-south-1.cache.amazonaws.com:6379
ENABLE_AWS_S3=true
S3_BUCKET=decibyl-prod-recordings
S3_REGION=ap-south-1
FASTAPI_WORKERS=8
DEFAULT_ORG_CONCURRENCY_LIMIT=60
DB_POOL_SIZE=20
DB_POOL_MAX_OVERFLOW=20
```

`DATABASE_URL` and `REDIS_URL` were only made overridable from `.env` in this
change. Before it, `docker-compose.yaml` named them under `environment:`, which
wins over `env_file` — so setting them in `.env` appeared to work, the stack
came up healthy, and it was still talking to the local container. Verify:

```bash
docker compose config | grep -E 'DATABASE_URL|REDIS_URL'
```

`DEFAULT_ORG_CONCURRENCY_LIMIT` ships at **10**. Left alone it caps the
campaign at 10 concurrent calls regardless of how much hardware you bought, and
presents as a campaign that runs slowly rather than as a limit being hit.

### 6.5 Load balancer

- Target group over ports 8000..8007 on both media nodes, health check
  `/api/v1/health`.
- **Idle timeout 300s.** The default 60s is shorter than a 100-second call.
  Media WebSockets carry constant traffic so they should not idle out — but the
  margin is not worth the argument.
- **Deregistration delay ≥ 180s**, above the longest call. Otherwise scale-in
  or a deploy cuts live conversations mid-sentence.
- Stickiness off. Each call's WebSocket runs its pipeline wherever it lands,
  and there is no session to keep.

---

## 7. Cutover

The current box holds real money — a live Razorpay ledger, issued receipt
vouchers, the platform credential store. Treat it as a data migration.

1. **Rotate `PLATFORM_CREDENTIAL_SECRET` on the way over.** It is in git
   history and in a shared transcript. The new box is the natural place for a
   new key; provider credentials must be re-entered after the rotation because
   Fernet cannot read what a different key encrypted.
2. `pg_dump` from the old box, restore into RDS, run `alembic upgrade head`.
   Rehearse the restore first — `scripts/rehearse_restore.sh` exists.
3. Copy MinIO objects to S3 (`aws s3 sync`). Recording URLs are presigned on
   read, so nothing has a baked-in host.
4. **Create a new Razorpay webhook** for the new public URL, in live mode, with
   its own secret. The old one keeps pointing at the old box and will silently
   go unanswered.
5. Cut DNS. Keep the old box **stopped, not terminated**, for a fortnight.
6. Re-run `scripts/e2e_smoke.py` and confirm `missing platform keys: []`.

---

## 8. The soak test — the one number nobody has

Every instance count in this document rests on **5 concurrent calls per vCPU**,
and that figure has never been measured. It could be 3, in which case the fleet
is undersized by 40%. It could be 15, in which case we are spending twice what
we need.

It is also worth remembering that **the platform has never placed a call.**
The load test and the first real call should happen in that order, and both
before the pilot.

The test, in increasing order of what it proves:

1. **One real call, end to end** — Plivo, managed keys, Telugu, a real handset.
   Everything below is meaningless until this passes.
2. **Synthetic concurrency.** Drive N simultaneous WebSocket sessions at the
   telephony endpoint with pre-recorded 8 kHz µ-law audio, no carrier involved.
   Ramp 5 → 10 → 20 → 40 → 60 on one `c7i.2xlarge`. Record at each step:
   - TTFB p50/p95 (already a first-class series — `/dashboard/latency`)
   - host CPU, and CPU per uvicorn process
   - DB pool checkout wait — the metric that catches exhaustion before latency
     does
   - dropped/errored sessions
3. **The knee** is where p95 TTFB rises with no matching CPU rise. That is
   contention, not saturation, and it is the real ceiling.
4. **A carrier smoke at 20 concurrent** against a small internal list, to
   confirm the SIP channel count and that the from-number pool spreads as
   intended.

Feed the measured ratio back into `CONCURRENT_CALLS_PER_VCPU` in
`scripts/infra_sizing.py` and re-read the fleet.

---

## 9. Adding VoiceLink

[VoiceLink](https://voicelink.co.in/) is an Indian SIP carrier aimed
specifically at voicebots — Indian mobile and landline DIDs, dedicated SIP
channels, DLT-compliant, Indian points of presence. That combination is
directly relevant here: the tender wants Indian data residency and a plausible
local caller ID, and our Plivo carrier rate (₹0.25/min) is still an assumption
with no written quote behind it.

They publish SIP, WebSocket and REST. Which one they give us decides the cost.

### Path A — SIP into the existing ARI provider. **Zero platform code.**

`providers/ari/` is a complete generic-SIP integration, and pipecat ships
`serializers/asterisk.py`. Stand up Asterisk, point a trunk at VoiceLink, and
configure it in the UI as an Asterisk ARI provider — endpoint, Stasis app name,
password, from-extensions.

This works **today**, with no new code and no release. The cost is that we now
run Asterisk: another instance, another thing to tune, another thing on call.
For a seven-day campaign that is a real but bounded cost, and it is the fastest
route to a written carrier quote.

### Path B — a native `voicelink` provider. **~1.5 engineer-days.**

Worth it if VoiceLink's media WebSocket is Plivo- or Twilio-compatible, which
most Indian providers' are. `providers/vobiz/` is the template: its transport
is 82 lines and its own comment says *"Vobiz uses Plivo-compatible WebSocket
protocol — MULAW audio at 8kHz, base64 in JSON."*

The provider architecture is built for this. Per
`api/services/telephony/providers/AGENTS.md`, a new provider is one directory
plus **exactly two lines** elsewhere:

```
providers/voicelink/
├── __init__.py     ProviderSpec + register()
├── config.py       Request/Response, both provider: Literal["voicelink"]
├── provider.py     outbound dial, hangup, status callbacks
├── transport.py    FastAPIWebsocketTransport + serializer
├── serializers.py  re-export, or subclass PlivoFrameSerializer
└── routes.py       answer URL, hangup/ring callbacks
```

1. `providers/__init__.py` — add `voicelink` to the import list.
2. `api/schemas/telephony_config.py` — add the request to the
   `TelephonyConfigRequest` union and the response to
   `TelephonyConfigurationResponse`.

Nothing in `factory.py`, `run_pipeline.py`, `routes/telephony.py` or the
frontend is touched — the config form renders itself from `ui_metadata`. If a
change needs one of those files, it has been done wrong.

The only real unknown is the frame serializer. If their protocol matches
Plivo's, subclass it and the work is a day. If it is genuinely bespoke, budget
a serializer of ~250 lines in the pipecat fork, matching `serializers/vobiz.py`.

### What to ask VoiceLink before committing

1. Media protocol — WebSocket JSON with base64 µ-law like Plivo/Twilio, or SIP
   only? **This single answer picks Path A or Path B.**
2. Per-minute outbound rate to Telangana mobile, in writing, at ~90,000
   billable minutes. This is the figure the ₹0.25 assumption needs.
3. Concurrent channel provisioning — can they carry 75 channels for one week,
   and what is the lead time?
4. DLT registration path for the campaign's sender/content, and whose account
   it sits under.
5. PoP location — Mumbai matters, because every 20 ms of carrier latency lands
   on top of STT, LLM and TTS in a conversation that is already tight.

---

## 10. Still unproven

Ordered by what would hurt most.

| | Status |
|---|---|
| The platform has never placed a call | **Blocking.** Nothing else counts until it has |
| 5 concurrent calls per vCPU | Estimate. §8 |
| Plivo India carrier rate | ₹0.25/min assumed; still seeded at the US ₹0.96. No written quote |
| Peak-to-average factor of 2.0 | Assumption. The pilot produces the real profile |
| Telangana Telugu quality | Needs a native listener on real output |
| ap-south-1 prices in the sizing model | Entered by hand; verify at calculator.aws |
| Retry day-part spreading | Not built. Flat intervals would miss the connection SLA |
| Dial-time calling-window enforcement | Enforced per batch, not per dial |
| Campaign aggregate report | Does not exist as a report |

The first row is the one to fix this week.
