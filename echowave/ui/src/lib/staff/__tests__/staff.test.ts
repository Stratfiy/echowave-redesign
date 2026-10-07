import { describe, expect, it } from "vitest";

import { orderAttention } from "../attention";
import { commandOf, count, DASH, money, percent, words } from "../format";
import { canOpen, classify, DESTINATIONS, firstAllowed, type Me } from "../nav";

function me(roles: string[], allowed: string[]): Me {
    return {
        user_id: 1,
        email: "s@example.test",
        tier: roles.includes("owner") ? "superadmin" : "support",
        mfa_enabled: true,
        roles,
        capabilities: [],
        destinations: DESTINATIONS.map((d) => ({ key: d.key, label: d.label, href: d.href, allowed: allowed.includes(d.key) })),
        environment: "staging",
        features: {},
    };
}

describe("the eight destinations", () => {
    it("are the handoff's eight, in order", () => {
        expect(DESTINATIONS.map((d) => d.key)).toEqual(["overview", "users", "support", "quality", "analytics", "revenue", "operations", "controls"]);
    });

    it("keep the existing screens reachable under them", () => {
        const hrefs = DESTINATIONS.flatMap((d) => d.children.map((c) => c.href));
        for (const existing of ["/superadmin/billing", "/superadmin/flags", "/superadmin/invites", "/superadmin/system", "/superadmin/verification", "/superadmin/provider-keys"]) {
            expect(hrefs).toContain(existing);
        }
    });

    it("show the support inbox as needs setup until that stream lands", () => {
        const support = DESTINATIONS.find((d) => d.key === "support")!;
        expect(support.children.find((c) => c.href === "/superadmin/support")?.needsSetup).toBeTruthy();
    });
});

describe("who can open a page", () => {
    it("lets finance into revenue but not into the old billing screens", () => {
        const finance = me(["support", "finance"], ["overview", "revenue"]);
        expect(canOpen(finance, "/superadmin/revenue/ledger")).toBe(true);
        expect(canOpen(finance, "/superadmin/billing")).toBe(false);
        expect(canOpen(finance, "/superadmin/users")).toBe(false);
    });

    it("keeps support's KYC queue", () => {
        const support = me(["support"], ["overview", "users", "support", "analytics", "operations"]);
        expect(classify("/superadmin/verification").destination).toBe("support");
        expect(canOpen(support, "/superadmin/verification")).toBe(true);
        expect(canOpen(support, "/superadmin/controls/roles")).toBe(false);
    });

    it("treats an unmapped staff page as owner-only", () => {
        expect(classify("/superadmin/something-new")).toEqual({ destination: null, legacy: true });
        expect(canOpen(me(["owner"], ["overview"]), "/superadmin/something-new")).toBe(true);
        expect(canOpen(me(["quality"], ["quality"]), "/superadmin/something-new")).toBe(false);
    });

    it("sends a person to the first destination they have", () => {
        expect(firstAllowed(me(["quality"], ["quality", "analytics"]))).toBe("/superadmin/quality");
    });
});

describe("figures", () => {
    it("never turn a missing value into zero", () => {
        expect(money(null)).toBe(DASH);
        expect(percent(null)).toBe(DASH);
        expect(count(undefined)).toBe(DASH);
        expect(money(0)).not.toBe(DASH);
    });

    it("keep exact paise and the currency", () => {
        expect(money(1180001)).toContain("11,800.01");
        expect(money(1050, "USD")).toContain("10.50");
    });

    it("read codes as words", () => {
        expect(words("outcome_unknown")).toBe("Outcome unknown");
        expect(commandOf("#42 refund.request [queued] x")).toBe(42);
        expect(commandOf("impersonation")).toBeNull();
    });
});

describe("the attention queue", () => {
    it("puts open exceptions first by severity, then clear, then unmeasured", () => {
        const ordered = orderAttention([
            { key: "a", title: "", count: 0, href: "", severity: "critical" },
            { key: "b", title: "", count: null, href: "", severity: "info", state: "needs_setup" },
            { key: "c", title: "", count: 3, href: "", severity: "warning" },
            { key: "d", title: "", count: 1, href: "", severity: "critical" },
        ]);
        expect(ordered.map((x) => x.key)).toEqual(["d", "c", "a", "b"]);
    });
});
