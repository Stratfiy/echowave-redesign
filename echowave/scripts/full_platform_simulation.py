"""Full-platform simulation: one customer's journey, then the staff console.

Drives the real HTTP API. Anything that returns 5xx is a bug in our code;
4xx is the API refusing something, which may well be correct, so those are
reported with their reason rather than counted as failures unless the step
is one a customer must be able to complete.
"""

import json
import os
import sys
import time
import uuid

import requests

BASE = os.getenv("SIM_BASE", "http://127.0.0.1:8100/api/v1")
STAMP = int(time.time())
USER_EMAIL = f"sim-user-{STAMP}@decibyl-sim.com"
ADMIN_EMAIL = f"sim-admin-{STAMP}@decibyl-sim.com"
PASSWORD = "Test-Passw0rd!"

RESULTS = []


def rec(phase, name, status, detail="", body=None):
    RESULTS.append(
        {
            "phase": phase,
            "name": name,
            "status": status,
            "detail": detail,
            "body": (body or "")[:400],
        }
    )
    mark = {"PASS": "PASS", "FAIL": "FAIL", "WARN": "WARN", "INFO": "INFO"}[status]
    line = f"  [{mark}] {name}"
    if detail:
        line += f" — {detail}"
    print(line, flush=True)


def call(phase, name, session, method, path, *, expect=(200, 201), **kw):
    """Issue a request and classify the outcome.

    A 5xx is always a failure: it means the server fell over rather than
    answering. A status outside ``expect`` but under 500 is a WARN carrying
    the API's own reason, because plenty of refusals here are correct
    (no carrier configured, no credit, feature not provisioned).
    """
    url = f"{BASE}{path}"
    try:
        r = session.request(method, url, timeout=60, **kw)
    except Exception as e:  # transport failure
        rec(phase, name, "FAIL", f"transport error: {e}")
        return None
    body = r.text
    if r.status_code >= 500:
        rec(phase, name, "FAIL", f"HTTP {r.status_code}", body)
    elif r.status_code in expect:
        rec(phase, name, "PASS", f"HTTP {r.status_code}")
    else:
        rec(phase, name, "WARN", f"HTTP {r.status_code}", body)
    return r


def jsonof(r):
    if r is None:
        return {}
    try:
        return r.json()
    except Exception:
        return {}


# ---------------------------------------------------------------------------
# Phase 1 — a customer, from signup to a live agent
# ---------------------------------------------------------------------------


def phase_customer(s):
    p = "customer"
    print("\n=== PHASE 1: CUSTOMER JOURNEY ===")

    r = call(
        p,
        "signup",
        s,
        "POST",
        "/auth/signup",
        json={"email": USER_EMAIL, "password": PASSWORD},
    )
    if r is None or r.status_code != 200:
        print("cannot continue without a signed-up user")
        return None
    data = jsonof(r)
    s.headers["Authorization"] = f"Bearer {data['token']}"
    org_id = data["user"].get("organization_id")
    rec(
        p,
        "organization provisioned at signup",
        "PASS" if org_id else "FAIL",
        f"org={org_id}",
    )

    # Log back in with the same credentials — signup issuing a token is not
    # proof that login works, and login is the path every returning user takes.
    s2 = requests.Session()
    r = call(
        p,
        "login with the same credentials",
        s2,
        "POST",
        "/auth/login",
        json={"email": USER_EMAIL, "password": PASSWORD},
    )
    if r is not None and r.status_code == 200 and jsonof(r).get("token"):
        rec(p, "login returns a usable token", "PASS")

    call(p, "GET current user", s, "GET", "/user/auth/user")
    call(p, "GET onboarding state", s, "GET", "/user/onboarding-state")
    call(p, "GET organization context", s, "GET", "/organizations/context")
    call(p, "GET organization preferences", s, "GET", "/organizations/preferences")

    for agreement in ("dpa", "terms"):
        call(
            p,
            f"accept {agreement}",
            s,
            "POST",
            "/privacy/agreements/accept",
            json={"agreement": agreement},
        )

    # The agent-creation wizard's own screens.
    call(p, "GET agent options", s, "GET", "/agent-options")
    cat = call(p, "GET agent option catalogue", s, "GET", "/agent-options/catalogue")
    call(
        p,
        "GET agent minutes",
        s,
        "GET",
        "/agent-options/minutes",
        params={"balance_paise": 50000, "brain": "lite"},
    )
    call(p, "GET carriage options", s, "GET", "/agent-options/carriage")
    call(p, "GET agent templates", s, "GET", "/agent-templates")
    call(p, "GET node types", s, "GET", "/node-types")

    voice, llm_tier = "karun", "lite"
    catalogue = jsonof(cat)
    if isinstance(catalogue, dict):
        voices = catalogue.get("voices") or []
        if voices and isinstance(voices[0], dict) and voices[0].get("id"):
            voice = voices[0]["id"]

    print("\n--- creating an agent through the wizard (writes a v3 stack) ---")
    r = call(
        p,
        "POST create agent from template",
        s,
        "POST",
        "/workflow/create/template",
        json={
            "call_type": "outbound",
            "use_case": "lead_qualification",
            "activity_description": "Qualify inbound farm-equipment leads.",
            "agent_name": "Simulation Agent",
            "company_name": "Decibyl Simulation",
            "languages": ["en"],
            "voice": voice,
            "llm_tier": llm_tier,
            "welcome_message": "Hello, this is a test agent.",
        },
    )
    wf = jsonof(r)
    workflow_id = wf.get("id") or (wf.get("workflow") or {}).get("id")
    if not workflow_id:
        rec(
            p,
            "wizard returned a workflow id",
            "FAIL",
            "no id in response",
            json.dumps(wf)[:400],
        )
        return None
    rec(p, "wizard returned a workflow id", "PASS", f"workflow_id={workflow_id}")

    # The regression this whole exercise is about: what shape did the wizard
    # store, and does everything downstream survive reading it?
    r = call(p, "GET the created workflow", s, "GET", f"/workflow/fetch/{workflow_id}")
    stored = jsonof(r)
    configs = stored.get("workflow_configurations") or {}
    override = configs.get("model_configuration_v2_override") or {}
    version = override.get("version")
    rec(
        p,
        "wizard stored a v3 model stack",
        "PASS" if version == 3 else "WARN",
        f"version={version}",
    )

    call(p, "GET workflow list", s, "GET", "/workflow/fetch")
    call(p, "GET workflow count", s, "GET", "/workflow/count")
    call(p, "GET workflow summary", s, "GET", "/workflow/summary")
    call(
        p,
        "POST validate workflow",
        s,
        "POST",
        f"/workflow/{workflow_id}/validate",
        json={},
    )
    call(p, "GET workflow versions", s, "GET", f"/workflow/{workflow_id}/versions")
    call(p, "GET workflow report", s, "GET", f"/workflow/{workflow_id}/report")
    call(p, "GET workflow runs", s, "GET", f"/workflow/{workflow_id}/runs")

    # Saving the agent again is the second place the v3 override is parsed —
    # update_workflow re-validates it and writes it back.
    call(
        p,
        "PUT update workflow (re-saves the v3 override)",
        s,
        "PUT",
        f"/workflow/{workflow_id}",
        json={"name": "Simulation Agent (renamed)", "workflow_configurations": configs},
    )

    r = call(p, "GET workflow after update", s, "GET", f"/workflow/fetch/{workflow_id}")
    after = (jsonof(r).get("workflow_configurations") or {}).get(
        "model_configuration_v2_override"
    ) or {}
    rec(
        p,
        "v3 override survived the update round-trip",
        "PASS" if after.get("version") == 3 else "FAIL",
        f"version={after.get('version')}",
    )

    call(
        p,
        "POST publish workflow",
        s,
        "POST",
        f"/workflow/{workflow_id}/publish",
        json={},
    )
    call(
        p,
        "PUT workflow live",
        s,
        "PUT",
        f"/workflow/{workflow_id}/live",
        json={"is_live": True},
    )

    print("\n--- call readiness: the production 500 ---")
    r = call(
        p,
        "POST initiate-call on the wizard-built agent",
        s,
        "POST",
        "/telephony/initiate-call",
        json={"workflow_id": workflow_id, "phone_number": "+919999999999"},
        expect=(200, 201),
    )
    if r is not None:
        body = r.text
        broke_on_v2_literal = (
            "OrganizationAIModelConfigurationV2" in body or "literal_error" in body
        )
        rec(
            p,
            "call readiness no longer rejects version=3",
            "FAIL" if broke_on_v2_literal else "PASS",
            f"HTTP {r.status_code}",
            body,
        )

    print("\n--- the rest of the product ---")
    call(
        p,
        "POST text-chat session",
        s,
        "POST",
        f"/workflow/{workflow_id}/text-chat/sessions",
        json={},
    )
    call(p, "GET knowledge base usage", s, "GET", "/knowledge-base/usage")
    call(p, "GET knowledge base documents", s, "GET", "/knowledge-base/documents")
    call(
        p,
        "POST knowledge base search",
        s,
        "POST",
        "/knowledge-base/search",
        json={"query": "warranty", "limit": 3},
    )

    r = call(
        p,
        "POST create a custom tool",
        s,
        "POST",
        "/tools/",
        json={
            "name": f"sim-tool-{STAMP}",
            "description": "simulation tool",
            "category": "http_api",
            "definition": {
                "type": "http_api",
                "method": "GET",
                "url": "https://example.invalid/hook",
            },
        },
    )
    tool_uuid = jsonof(r).get("uuid") or jsonof(r).get("tool_uuid")
    call(p, "GET tools", s, "GET", "/tools/")
    if tool_uuid:
        call(p, "DELETE the custom tool", s, "DELETE", f"/tools/{tool_uuid}")

    call(p, "GET credentials", s, "GET", "/credentials/")
    call(p, "GET telephony configs", s, "GET", "/organizations/telephony-configs")
    call(
        p,
        "POST create telephony config",
        s,
        "POST",
        "/organizations/telephony-configs",
        json={
            "name": f"sim-plivo-{STAMP}",
            "is_default_outbound": True,
            "config": {
                "provider": "plivo",
                "auth_id": "MASIMULATIONID",
                "auth_token": "sim-token",
            },
        },
        expect=(200, 201, 400, 422),
    )
    call(
        p,
        "GET telephony provider metadata",
        s,
        "GET",
        "/organizations/telephony-providers/metadata",
    )
    call(
        p,
        "GET telephony config warnings",
        s,
        "GET",
        "/organizations/telephony-config-warnings",
    )
    call(p, "GET verified numbers", s, "GET", "/verified-numbers")
    call(p, "GET do-not-call list", s, "GET", "/do-not-call")
    call(p, "GET campaigns", s, "GET", "/campaign/")
    call(
        p,
        "POST create campaign (no contact source yet)",
        s,
        "POST",
        "/campaign/create",
        json={
            "name": f"sim-campaign-{STAMP}",
            "workflow_id": workflow_id,
            "source_type": "csv",
            "source_id": str(uuid.uuid4()),
        },
        expect=(200, 201, 400, 404, 422),
    )
    call(p, "GET campaign defaults", s, "GET", "/organizations/campaign-defaults")
    call(p, "GET folders", s, "GET", "/folder")
    call(
        p,
        "GET google-calendar integration status",
        s,
        "GET",
        "/integrations/google-calendar/status",
    )
    call(p, "GET workflow recordings", s, "GET", "/workflow-recordings/")

    call(p, "GET billing balance", s, "GET", "/billing/balance")
    call(p, "GET billing payments", s, "GET", "/billing/payments")
    call(
        p,
        "POST per-minute cost estimate",
        s,
        "POST",
        "/cost-estimate/per-minute",
        json={},
    )
    call(p, "GET subscription plan", s, "GET", "/billing/plan")
    call(p, "GET billing profile", s, "GET", "/billing/profile")
    call(p, "GET billing mandate", s, "GET", "/billing/mandate")
    call(p, "GET billing documents", s, "GET", "/billing/documents")
    call(p, "GET KYC state", s, "GET", "/kyc")
    call(p, "GET org members", s, "GET", "/organizations/members")
    call(
        p,
        "GET org model configuration v2",
        s,
        "GET",
        "/organizations/model-configurations/v2",
    )
    call(
        p,
        "GET daily report",
        s,
        "GET",
        "/organizations/reports/daily",
        params={"date": time.strftime("%Y-%m-%d"), "timezone": "Asia/Kolkata"},
    )
    call(p, "GET workflow report (org)", s, "GET", "/organizations/reports/workflows")
    call(p, "GET api keys", s, "GET", "/user/api-keys")
    call(p, "GET service keys", s, "GET", "/user/service-keys")
    call(p, "GET turn credentials", s, "GET", "/turn/credentials")
    call(p, "GET privacy readiness", s, "GET", "/privacy/readiness")

    return workflow_id


# ---------------------------------------------------------------------------
# Phase 2 — the staff console
# ---------------------------------------------------------------------------


def phase_superadmin(workflow_id):
    p = "superadmin"
    print("\n=== PHASE 2: SUPER ADMIN ===")
    s = requests.Session()
    r = call(
        p,
        "admin signup",
        s,
        "POST",
        "/auth/signup",
        json={"email": ADMIN_EMAIL, "password": PASSWORD},
    )
    if r is None or r.status_code != 200:
        return
    s.headers["Authorization"] = f"Bearer {jsonof(r)['token']}"

    # Staff access is granted out of band by scripts/grant_superuser.py; a
    # signup can never grant it to itself, which is the point.
    rc = os.system(
        f"cd /home/user/echowave-redesign/echowave && "
        f"python -m scripts.grant_superuser {ADMIN_EMAIL} > /tmp/grant.log 2>&1"
    )
    rec(p, "grant_superuser script", "PASS" if rc == 0 else "FAIL", f"rc={rc}")

    # The grant lands in the database; the session must be re-issued to carry it.
    s = requests.Session()
    r = call(
        p,
        "admin re-login after grant",
        s,
        "POST",
        "/auth/login",
        json={"email": ADMIN_EMAIL, "password": PASSWORD},
    )
    if r is None or r.status_code != 200:
        return
    s.headers["Authorization"] = f"Bearer {jsonof(r)['token']}"

    call(p, "GET billing overview", s, "GET", "/admin/billing/overview")
    call(p, "GET billing readiness", s, "GET", "/admin/billing/readiness")
    call(p, "GET billing accounts", s, "GET", "/admin/billing/accounts")
    call(p, "GET rate card", s, "GET", "/admin/billing/rate-card")
    call(p, "GET rate card markup", s, "GET", "/admin/billing/rate-card/markup")
    call(p, "GET providers", s, "GET", "/admin/billing/providers")
    call(p, "GET unit economics", s, "GET", "/admin/billing/unit-economics")
    call(p, "GET pricing inputs", s, "GET", "/admin/billing/pricing-inputs")
    call(p, "GET bundles", s, "GET", "/admin/billing/bundles")
    call(p, "GET bundle economics", s, "GET", "/admin/billing/bundles/economics")
    call(p, "GET managed tiers", s, "GET", "/admin/billing/managed-tiers")
    call(
        p,
        "GET managed tier choices",
        s,
        "GET",
        "/admin/billing/managed-tiers/choices",
        params={"component": "llm"},
    )
    call(p, "GET admin plans", s, "GET", "/admin/billing/plans")
    call(p, "GET admin calls", s, "GET", "/admin/billing/calls")
    call(p, "GET admin campaigns", s, "GET", "/admin/billing/campaigns")
    call(p, "GET admin payments", s, "GET", "/admin/billing/payments")
    call(p, "GET admin model usage", s, "GET", "/admin/billing/model-usage")
    call(p, "GET admin latency", s, "GET", "/admin/billing/latency")
    call(p, "GET admin tokens", s, "GET", "/admin/billing/tokens")
    call(p, "GET admin retention", s, "GET", "/admin/billing/retention")
    call(p, "GET admin activation", s, "GET", "/admin/billing/activation")

    call(p, "GET provider keys", s, "GET", "/admin/provider-keys")
    call(p, "GET provider key catalogue", s, "GET", "/admin/provider-keys/catalogue")
    call(p, "GET provider key providers", s, "GET", "/admin/provider-keys/providers")
    call(
        p,
        "GET provider key models",
        s,
        "GET",
        "/admin/provider-keys/models",
        params={"component": "llm", "provider": "openai"},
    )

    call(p, "GET KYC queue", s, "GET", "/admin/kyc/queue")
    call(p, "GET partner queue", s, "GET", "/admin/partners/queue")
    call(p, "GET partner statements", s, "GET", "/admin/partners/statements")
    call(
        p,
        "GET admin telephony configurations",
        s,
        "GET",
        "/admin/telephony/configurations",
    )
    call(p, "GET admin shared outbound", s, "GET", "/admin/telephony/shared-outbound")
    call(p, "GET superuser workflow runs", s, "GET", "/superuser/workflow-runs")

    # A staff account is not a customer's account. Reaching another org's
    # workflow through a customer route must not succeed just because the
    # caller is staff.
    if workflow_id:
        r = call(
            p,
            "staff cannot read a customer workflow via the customer route",
            s,
            "GET",
            f"/workflow/fetch/{workflow_id}",
            expect=(404, 403),
        )
        if r is not None and r.status_code == 200:
            rec(
                p,
                "tenant isolation on /workflow/fetch/{id}",
                "FAIL",
                "staff account read another org's workflow",
            )
        elif r is not None:
            rec(
                p,
                "tenant isolation on /workflow/fetch/{id}",
                "PASS",
                f"HTTP {r.status_code}",
            )


def summary():
    print("\n" + "=" * 70)
    counts = {}
    for r in RESULTS:
        counts[r["status"]] = counts.get(r["status"], 0) + 1
    print("  ".join(f"{k} {v}" for k, v in sorted(counts.items())))
    fails = [r for r in RESULTS if r["status"] == "FAIL"]
    warns = [r for r in RESULTS if r["status"] == "WARN"]
    if fails:
        print("\nFAILURES (5xx or a step a customer must be able to complete):")
        for r in fails:
            print(f"  - [{r['phase']}] {r['name']}: {r['detail']}")
            if r["body"]:
                print(f"      {r['body'][:300]}")
    if warns:
        print("\nWARNINGS (the API refused; may be correct):")
        for r in warns:
            print(f"  - [{r['phase']}] {r['name']}: {r['detail']} {r['body'][:160]}")
    with open("/tmp/sim_results.json", "w") as fh:
        json.dump(RESULTS, fh, indent=2)
    print("\nfull results: /tmp/sim_results.json")
    return len(fails)


if __name__ == "__main__":
    s = requests.Session()
    wf = phase_customer(s)
    phase_superadmin(wf)
    sys.exit(1 if summary() else 0)
