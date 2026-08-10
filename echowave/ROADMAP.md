# Launch roadmap and running log

Two things in one file, deliberately: the ordered plan, and the live state of
it. A separate progress file drifts from the plan it tracks.

**If you are an agent picking this up cold, read §0 first.** It is written for
exactly that.

---

## 0. Cold start — read this first

### Where things are

| | |
|---|---|
| Repo root | `/home/user/echowave-redesign` — note `.git` is here, `docker-compose.yaml` is one level down in `echowave/` |
| Working dir | `/home/user/echowave-redesign/echowave` |
| Branch | `claude/pricing-correctness` |
| Live box | `52.200.151.15` — app/api/docs/apex all resolve here |

### Running the tests

Postgres and Redis **die frequently in this container**. When a test run shows
`Connect call failed ('127.0.0.1', 5432)` — that is infrastructure, not your
code. Restart and re-run:

```bash
pg_ctlcluster 16 main start; redis-server --daemonize yes --save ''; sleep 3
```

Then:

```bash
cd /home/user/echowave-redesign/echowave
source venv/bin/activate
export PYTHONPATH=. \
  DATABASE_URL="postgresql+asyncpg://postgres:postgres@127.0.0.1:5432/decibyl_test" \
  REDIS_URL="redis://127.0.0.1:6379/0" \
  ENABLE_AWS_S3=false MINIO_PUBLIC_ENDPOINT=http://localhost:9000 DEPLOYMENT_MODE=oss
python -m pytest api/tests -q --no-header -p no:randomly
```

There is no `api/.env.test` in this container despite what `AGENTS.md` says —
export the variables directly as above.

### Known-failing tests — NOT yours

Six fail on a clean `origin/main`. Verified against a clean worktree on a
separate database. Do not chase them:

- `test_mcp_tool_creation.py::test_sdk_openapi_exposes_create_tool_schema_and_llm_hints`
- `test_user_idle_handler.py::test_idle_does_not_trigger_during_active_conversation`
- `integrations/test_run_pipeline_text_greeting.py::test_text_greeting_speaks_then_user_transcript_triggers_end_call`
- `test_pipecat_engine_tool_calls.py` — three parallel-transition tests

Baseline is **2596 passed, 6 failed**.

### Docs

```bash
cd docs && npm run build && npm run check-links
```

`docs.json` is the single nav source — it drives both the rendered sidebar and
the MCP docs tools. Adding a page means adding it there.

### What is confirmed working in production

Tested by the operator, not just by tests: **outbound calls**, and the
**Google Calendar tool**.

---

## 1. The plan, in order

Ordered by what unblocks the most. **Payments and autopay are deliberately
last** — prepaid plus the dunning schedule already works, and nothing else
waits on them.

| # | Item | Est | State |
|---|---|---|---|
| 1 | Admin route for `is_platform_managed` | 0.5d | ✅ done |
| 2 | Customer token & spend dashboard | 1.5d | ☐ |
| 3 | Call-log graphs and metrics | 1d | ☐ |
| 4 | Provider markup (the 1.3×) | 1d | ☐ |
| 5 | Number provisioning UI | 1.5d | ☐ |
| 6 | Low-balance email | 0.5d | ☐ |
| 7 | *(later)* Razorpay autopay / e-mandate | 3–4d | ☐ |
| 8 | *(later)* Invoice PDF | 1d | ☐ |

Each item is done only when it is **built, tested, and verified against a real
request** — not when it compiles.

---

## 2. Why this order

**1 before everything.** Nothing in the managed-number path is reachable
without it. Provisioning, rental billing and the KYC gate are all built and all
dormant because one flag has no setter.

**2 and 3 before 4.** You cannot price confidently against numbers you cannot
see. The token dashboard already exists for superadmins — `/tokens` even
accepts an `organization_id` — so this is a scoped route and a page, not a
rebuild.

**4 before 5.** Charging correctly matters more than a nicer way to buy.

**6 before 7.** A customer whose number is suspended at day 7 with no email is
the worst version of the dunning schedule. The email is worth more than the
mandate for MVP, at a tenth of the cost.

**7 last.** Razorpay e-mandate is a separate product with its own activation —
partly outside our control, and prepaid works today.

---

## 3. Running log

Newest last. Each entry: what changed, how it was verified, what is next.

### 2026-08-10 — baseline

Managed numbers (provisioning, Plivo compliance, rental billing, dunning,
release, reconciliation), 4 read-only MCP telephony tools, docs migrated to
Astro Starlight and served from nginx, apex served empty, subdomain config
rendered by `decibyl-init`.

Verified: 2596 tests pass (6 pre-existing failures); docs build 130 pages with
16,954 links and none broken; all four hostnames probed through real nginx.

Not proven: **nothing has run against Plivo's live Compliance API.** No
application filed, no number bought.

Next: item 1.

### 2026-08-10 — item 1 done: `is_platform_managed` has a setter

`api/routes/telephony_admin.py`, mounted at `/api/v1/admin/telephony`, behind
the superuser gate declared at router level. Two endpoints: list the
configurations staff administer, and set or clear the managed flag.

The part worth knowing is the refusal. **Clearing the flag is rejected with 409
while numbers we bought are still attached**, and names them. The flag is what
marks a number as ours to bill and ours to release, so clearing it with rentals
outstanding leaves us paying a carrier with nothing pointing at the money — the
same orphaned-rental failure the reconciliation job exists to catch, except
caused by us. A released number does not block, and neither does a customer's
own number (no `carrier_number_id` means we never bought it).

Verified: 11 tests. The one that matters most asserts the *effect* rather than
the write — a configuration is ungated before the flag is set and raises
`TelephonyNotVerified` after, which is the whole reason the flag exists.

Next: item 2, the customer token and spend dashboard. The backend is largely
there — `dash.token_usage_series` and `token_usage_by_model` already accept an
`organization_id`; what is missing is a customer-scoped route (the existing one
is superadmin-only) and a page.
