# AWS: grow without re-platforming, and keep people's data safe

Written 30 September 2026 for the 4 October launch and the months after it. Owner of every AWS step: Nithish. Owner of every code step: the build team (Claude).

## The one idea

**The containers never change; only where the state lives changes.** Today one EC2 box runs the whole compose stack, with Postgres, Redis and MinIO inside it. Each stage below moves one piece of state to a managed AWS service, through an env var, with the same images. Nothing is rewritten, and each step can be reversed by changing the env var back.

## Stage 0: before 4 October (one box, made safe)

| Step | Where | Why |
|---|---|---|
| Region `ap-south-1` (Mumbai) for everything that holds customer data | EC2, S3, snapshots | DPDP and customer expectation: Indian data stays in India. Already the default in `cutover_to_managed_postgres.sh`. |
| EBS volume encrypted (gp3), and a Data Lifecycle Manager policy: daily snapshot, keep 7 daily + 4 weekly | EC2 → Lifecycle Manager | The box is a single point of failure. A snapshot is the fastest way back. An unencrypted volume cannot be encrypted in place: snapshot → copy with encryption → new volume. |
| AWS Budgets: alert at 50/80/100% of a monthly figure you choose, to email and phone. Turn on Cost Anomaly Detection | Billing | The Free Tier page shows the free allowances are used up: EBS 30 GB-Mo at 100%, the public IPv4 address at 94%, regional data transfer at 100%. Anything beyond those is billed from here on. That is expected, but it needs an alarm, not a surprise. |
| Security group: 80/443 open, SIP/RTP ports only to the carrier's IP ranges, **no port 22 to the world** | EC2 → Security groups | Use Systems Manager Session Manager for shell access instead of SSH. |
| Instance IAM role instead of access keys on the box | IAM | No long-lived AWS keys in `.env`. |
| CloudTrail on (all regions, to an S3 bucket), GuardDuty on | CloudTrail, GuardDuty | Who did what in the account, and an alert on compromise. Both cost little at this size. |
| Root account: MFA, no access keys; a separate IAM user for daily work | IAM | Basic hygiene. |
| Registry pulls instead of building on the box: give the box a read-only GHCR token (`docker login ghcr.io`) and allow outbound 443 to ghcr.io, then set `IMAGE_SOURCE=registry` in `.env` | Box `.env` | Builds stop eating the CPU that live calls use. PR #501 then keeps only the last three releases on disk. Rollback becomes `IMAGE_TAG=<sha>`. |

**Clearing old images today**, on the box, once:

```
docker system df                       # what is using space
docker image ls | grep decibyl         # every release still on disk
docker image prune -f                  # untagged leftovers (safe)
docker builder prune -f --keep-storage 20GB
```

Do **not** run `docker image prune -a` or `docker system prune -a`. They delete the previous release's image, which is the rollback, and base images the next deploy needs.

## Stage 1: first paying customers (move state off the box)

The box becomes disposable: it can be replaced with no data loss.

**Status on 30 September.** The September bill shows RDS (USD 46.71) and ElastiCache (USD 12.71) already running next to EC2 (USD 143.91), with S3 at USD 0.00. Before anything else, confirm the app actually uses them. If `DATABASE_URL` or `REDIS_URL` in the box's `.env` still point at the bundled containers, we pay twice and the managed copies protect nothing. Check on the box (the output shows hostnames, not passwords, but paste it nowhere):

```
grep -E '^(DATABASE_URL|REDIS_URL|ENABLE_AWS_S3)=' .env | sed -E 's#//[^@]*@#//***@#'
docker compose ps postgres redis minio
```

- **Hosts end in `rds.amazonaws.com` and `cache.amazonaws.com`:** done. Stop the bundled `postgres` and `redis` containers only after a week of clean backups.
- **Hosts are `postgres` and `redis`:** run the cutover script below at a quiet hour. It is ordered so no ledger row is lost.
- **Either way:** check that the RDS instance has storage encryption, automated backups of at least 7 days, deletion protection, and **no public access**. Check that ElastiCache has in-transit encryption and an AUTH token.

Recordings are still on MinIO inside the box (S3 is USD 0.00). That is the one piece of customer data left on the box, and moving it is the next step.

| Piece | Move to | Switch |
|---|---|---|
| Postgres (with pgvector) | RDS PostgreSQL 16/17, storage-encrypted at creation (this cannot be added later), automated backups 7–14 days, PITR on | `scripts/cutover_to_managed_postgres.sh` exists and is ordered for zero ledger loss. Then `DATABASE_URL`. |
| Recordings, files (MinIO) | S3 bucket in ap-south-1: Block Public Access on, SSE-KMS default encryption, versioning, lifecycle rules matching the retention settings | `ENABLE_AWS_S3=true` + bucket vars. |
| Redis | ElastiCache Redis with TLS | `REDIS_URL=rediss://…`. TLS verification ships in #493. |
| Secrets | SSM Parameter Store (SecureString) or Secrets Manager, read into `.env` at boot | No secrets living only in a hand-edited file. |

## Stage 2: growth (split the two halves)

`AGENTS.md` explains that voice is stateful and CPU-bound while everything else is an ordinary web app. Split them accordingly:

- **Web tier** (api, ui, ARQ workers for chat, routines and WhatsApp): an Auto Scaling group of 2+ behind an Application Load Balancer, in two availability zones. Same images.
- **Voice tier**: separate EC2 instances (compute-optimised c7i) sized with `scripts/infra_sizing.py`, scaled on concurrent calls, never on CPU alone. Drained, never killed, during a deploy (`scripts/rolling_update.sh`).
- RDS Multi-AZ; a read replica for reports.

## Stage 3: later

ECS on EC2 for the web tier and Terraform for the account. Only when the team is large enough to run it. Terraform starts in Stage 1 at the latest, for new resources.

## Privacy, consent and deletion

The audit of 30 September found most of the groundwork already built. Evidence file:line is on the issue.

**Already in the code:**
- Terms and Privacy acceptance recorded at email signup (version, IP, user agent).
- DPA acceptance for campaigns and number purchase.
- AI and recording disclosure on every call, on by default.
- DND and calling-window checks.
- Erasure by phone number (a hard delete that removes recordings first).
- Data export.
- Per-org retention with a nightly job (recordings 90 days, transcripts 365, logs 7).
- Customer keys encrypted.
- Encrypted backups.
- A public `/trust` page with sub-processors.
- `GET /privacy/readiness` self-check.

**Gaps, in the order they are being closed:**

| # | Gap | Fix | Owner | By |
|---|---|---|---|---|
| 1 | Sentry receives personal data (`send_default_pii=True`, no scrubbing) | Off, plus a `before_send` that drops bodies and cookies. The same in the three UI configs | Code | 2 Oct |
| 2 | Google signups store no Terms/Privacy acceptance | Record it right after the account is provisioned | Code | 2 Oct |
| 3 | The DPA is only asked for before campaigns and numbers | Show the existing agreements dialog once, app-wide, until accepted | Code | 2 Oct |
| 4 | No way for a customer to delete their account or workspace | Owner-only "Delete workspace" on `/privacy`: confirm by typing the name, 7-day grace period, then erase contacts, knowledge files and their embeddings, runs, recordings; members deactivated; billing records kept as the law requires. **For 4 Oct:** the privacy notice states "email the grievance officer; done within 30 days" | Code + Nithish (notice text) | Notice 3 Oct; button the week after |
| 5 | Deleting a knowledge file keeps the file and its embeddings; deleting a contact keeps their recordings | Hard delete both | Code | Week after launch |
| 6 | No WhatsApp STOP handling or opt-in record | STOP/UNSUBSCRIBE adds the number to do-not-contact; day 1 sends utility templates only | Code | Before WhatsApp turns on |
| 7 | Config that can leave data unprotected | Refuse to start in production without `PLATFORM_CREDENTIAL_SECRET` and the grievance officer's name and address. S3 SSE-KMS (Stage 1) | Code + Nithish | 3 Oct |
| 8 | Backups outlive an erasure by up to 30 days | State it in the privacy notice | Nithish | 3 Oct |

**Nithish, legal and text (LAUNCH-4):**
- Publish Terms and a Privacy Notice at in-app routes.
- Name the grievance officer (DPDP s.13) and set `GRIEVANCE_OFFICER_NAME` / `GRIEVANCE_OFFICER_ADDRESS`.
- List the sub-processors (the `/trust` page generates the list from the code; check it reads right).
- Sign DPAs with the model, STT and TTS vendors.
- Run `/privacy/readiness` on production on 3 October and paste the result on the LAUNCH epic.
