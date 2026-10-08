import { mkdirSync } from "node:fs";

import { expect, type Page, test } from "@playwright/test";

import { dismissPrompts, offScreen } from "./fixtures";

/**
 * The staff console, phase 3: every staff screen opens for a staff account
 * at a phone's width and a laptop's, without the page scrolling sideways,
 * and every one of them refuses a workspace owner and a plain member.
 *
 *   STAFF_EMAIL / STAFF_PASSWORD     a superadmin
 *   OWNER_EMAIL / OWNER_PASSWORD     a workspace owner, not staff
 *   MEMBER_EMAIL / MEMBER_PASSWORD   a plain member, not staff
 *   STAFF_SCREENSHOTS                optional directory for a screenshot of each screen
 *   STAFF_USER_ID, STAFF_TICKET_ID, STAFF_CALL_ID, STAFF_ORG_ID   records to open (default 2, 1, 1, 2)
 *
 * Skipped when the accounts are not given, so the staging run is unchanged.
 */

const env = process.env;
const accounts = {
    staff: [env.STAFF_EMAIL, env.STAFF_PASSWORD],
    owner: [env.OWNER_EMAIL, env.OWNER_PASSWORD],
    member: [env.MEMBER_EMAIL, env.MEMBER_PASSWORD],
} as const;
const configured = Object.values(accounts).every(([e, p]) => e && p);
const shots = env.STAFF_SCREENSHOTS;

const user = env.STAFF_USER_ID || "2";
const ticket = env.STAFF_TICKET_ID || "1";
const call = env.STAFF_CALL_ID || "1";
const org = env.STAFF_ORG_ID || "2";

/** Every staff screen, with something on it that proves it drew. */
const SCREENS: Array<{ path: string; name: string; shows: RegExp }> = [
    { path: "/superadmin/overview", name: "overview", shows: /Overview|attention/i },
    { path: "/superadmin/users", name: "users", shows: /People|waitlist|Users/i },
    { path: `/superadmin/users/${user}`, name: "user-detail", shows: /Workspace/i },
    { path: "/superadmin/support", name: "support-inbox", shows: /Support|Inbox|queue/i },
    { path: `/superadmin/support/${ticket}`, name: "support-case", shows: /reminder|Reply|note/i },
    { path: "/superadmin/support/actions", name: "support-actions", shows: /action/i },
    { path: "/superadmin/quality", name: "quality", shows: /evaluation|Quality/i },
    { path: "/superadmin/analytics", name: "analytics", shows: /Activation|analytics/i },
    { path: "/superadmin/revenue", name: "revenue", shows: /Revenue|cost/i },
    { path: "/superadmin/revenue/ledger", name: "ledger", shows: /Ledger|refund/i },
    { path: "/superadmin/operations?tab=jobs", name: "operations-jobs", shows: /Operations/i },
    { path: "/superadmin/operations?tab=calls", name: "operations-calls", shows: /Active calls/i },
    { path: "/superadmin/operations?tab=infrastructure", name: "operations-infrastructure", shows: /Analytics outbox/i },
    { path: "/superadmin/operations/latency", name: "voice-latency", shows: /latency/i },
    { path: "/superadmin/operations/incidents", name: "incidents", shows: /Incident/i },
    { path: "/superadmin/telephony", name: "telephony", shows: /Phone numbers and telephony/i },
    { path: "/superadmin/telephony/shared-outbound", name: "shared-outbound", shows: /shared|outbound/i },
    { path: "/superadmin/billing/calls", name: "calls", shows: /Call/i },
    { path: `/superadmin/billing/calls/${call}`, name: "call-detail", shows: /Transcript and recording/i },
    { path: "/superadmin/billing/campaigns", name: "campaigns", shows: /Campaign/i },
    { path: `/superadmin/billing/accounts/${org}`, name: "workspace-account", shows: /Credit|balance|Organization/i },
    { path: "/superadmin/controls/providers", name: "providers", shows: /Provider/i },
    { path: "/superadmin/controls/policy", name: "policy", shows: /Flags, budgets and model policy/i },
    { path: "/superadmin/controls/routing", name: "routing", shows: /Routing, cost stop and rollbacks/i },
    { path: "/superadmin/controls/roles", name: "roles", shows: /role/i },
    { path: "/superadmin/controls/audit", name: "audit", shows: /Audit/i },
    { path: "/superadmin/flags", name: "flags", shows: /flag/i },
    { path: "/superadmin/provider-keys", name: "provider-keys", shows: /key/i },
];

async function signInAs(page: Page, who: keyof typeof accounts) {
    const [email, password] = accounts[who];
    // A development server compiles each screen on its first visit.
    page.setDefaultNavigationTimeout(180_000);
    page.setDefaultTimeout(60_000);
    await page.goto("/auth/login");
    await page.getByTestId("login-email-input").fill(email!);
    await page.getByTestId("login-next").click();
    await page.getByTestId("login-password-input").fill(password!);
    await page.getByTestId("login-submit-btn").click();
    await page.waitForURL((url) => !url.pathname.startsWith("/auth/"), { timeout: 180_000 });
    if (new URL(page.url()).pathname.startsWith("/welcome")) {
        await page.getByRole("button", { name: "Skip for now" }).click();
        await page.waitForURL((url) => !url.pathname.startsWith("/welcome"));
    }
    await dismissPrompts(page);
}

test.describe("staff console", () => {
    test.skip(!configured, "STAFF_*, OWNER_* and MEMBER_* accounts are not set");

    test("every staff screen opens for staff, fits the width and shows real data", async ({ page }, info) => {
        test.setTimeout(15 * 60_000);
        if (shots) mkdirSync(shots, { recursive: true });
        await signInAs(page, "staff");
        const problems: string[] = [];
        page.on("pageerror", (error) => problems.push(`${page.url()}: ${String(error).slice(0, 300)}`));
        for (const screen of SCREENS) {
            await page.goto(screen.path);
            await expect(page.getByTestId("staff-shell"), screen.path).toBeVisible();
            await expect(page.locator("main"), screen.path).toContainText(screen.shows);
            await expect(page.getByRole("alert").filter({ hasText: /not available|does not include/i }), screen.path).toHaveCount(0);
            await page.waitForLoadState("networkidle").catch(() => undefined);
            expect(await offScreen(page), `${screen.path} fits ${info.project.name}`).toEqual([]);
            if (shots) await page.screenshot({ path: `${shots}/${info.project.name}-${screen.name}.png`, fullPage: true });
        }
        expect(problems).toEqual([]);
    });

    for (const who of ["owner", "member"] as const) {
        test(`a workspace ${who} is refused every staff screen`, async ({ page }) => {
            test.setTimeout(10 * 60_000);
            await signInAs(page, who);
            for (const screen of SCREENS) {
                await page.goto(screen.path);
                await page.waitForLoadState("networkidle").catch(() => undefined);
                const path = new URL(page.url()).pathname;
                const refusedByRedirect = !path.startsWith("/superadmin");
                const refusedOnPage = (await page.getByText(/not available|Your account does not have access/i).count()) > 0;
                expect(refusedByRedirect || refusedOnPage, `${who} at ${screen.path} landed on ${path}`).toBe(true);
                await expect(page.getByTestId("staff-shell")).toHaveCount(0);
            }
        });
    }
});
