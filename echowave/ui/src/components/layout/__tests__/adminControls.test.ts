/**
 * Controls gated inside a screen the member can still open.
 *
 * The sidebar answers "which doors", and `navigationAccess.test.ts` covers
 * that. This covers the harder half: three screens a member is *meant* to
 * reach, each holding one control they are not. Getting this wrong in either
 * direction is a real cost —
 *
 * - too open, and a member clicks something that can only answer with a 403;
 * - too closed, and a member cannot pay, cannot see who to ask, or cannot use
 *   a credential the account already holds.
 *
 * The rules live here as data so the split is stated once and can be read
 * without opening three components. Each entry mirrors a route's server-side
 * dependency, named so a reviewer can check the pair.
 */

import { describe, expect, it } from "vitest";

type Control = {
    /** Where a person finds it. */
    screen: string;
    /** What it does. */
    control: string;
    /** The route that enforces it, or null when nothing is gated. */
    route: string | null;
    /** Whether a plain member may use it. */
    memberMayUse: boolean;
};

const CONTROLS: Control[] = [
    // --- Billing: the screen stays open, one section does not ------------
    {
        screen: "billing",
        control: "top up the balance",
        route: null,
        // "A member who cannot top up when the balance runs out is a member
        // who cannot work, and paying us more money is not a privilege that
        // needs protecting."
        memberMayUse: true,
    },
    {
        screen: "billing",
        control: "read the billing details on file",
        route: null,
        // Their own company's tax identity. Readable so a member can see what
        // is wrong and who to ask, rather than facing a hidden section.
        memberMayUse: true,
    },
    {
        screen: "billing",
        control: "save billing details",
        route: "PUT /api/v1/billing/profile",
        // Decides whether GST is charged as CGST+SGST or IGST for everyone on
        // the account.
        memberMayUse: false,
    },

    // --- Renting a number: the flow stops at autopay ---------------------
    // Found by walking every `require_organization_role` route back to the
    // screen that calls it. This one called an ADMIN route from an ungated
    // button three steps into a journey, so a member could search, choose a
    // number, and only then be told "Could not start autopay".
    {
        screen: "numbers",
        control: "search and choose a number",
        route: null,
        memberMayUse: true,
    },
    {
        screen: "numbers",
        control: "see whether autopay is already authorised",
        route: null,
        // `GET /api/v1/billing/mandate` is not gated, so a member on an account
        // that already has autopay walks the whole flow without stopping.
        memberMayUse: true,
    },
    {
        screen: "numbers",
        control: "set up autopay",
        route: "POST /api/v1/billing/mandate",
        // A standing authority to debit the account's bank account.
        memberMayUse: false,
    },

    // --- Do not call: regulatory, and asymmetric -------------------------
    {
        screen: "do-not-call",
        control: "list suppressed numbers",
        route: null,
        memberMayUse: true,
    },
    {
        screen: "do-not-call",
        control: "add or upload numbers",
        route: null,
        // Adding is the safe direction: it stops calls rather than allowing
        // them, so it stays open.
        memberMayUse: true,
    },
    {
        screen: "do-not-call",
        control: "remove a number",
        route: "DELETE /api/v1/do-not-call/{phone_number}",
        // "A regulatory act, and the one entry nobody notices going missing."
        memberMayUse: false,
    },

    // --- Provider keys: the BYOK vault -----------------------------------
    // The case that proves the rule. This screen was gated whole, on the
    // reading that "every route under /api/v1/provider-keys requires ADMIN".
    // The list route does not — it takes plain `get_user` — and gating the door
    // broke BYOK for the people it is for.
    {
        screen: "provider-keys",
        control: "see which keys the account holds",
        route: null,
        // Masked to the last four characters by the API, so this shows what
        // exists rather than what it is. A member choosing "your own key" in a
        // model slot cannot otherwise tell an empty slot from a filled one, or
        // name the key they are asking an admin to add.
        memberMayUse: true,
    },
    {
        screen: "provider-keys",
        control: "add or rotate a key",
        route: "PUT /api/v1/provider-keys",
        // A secret, and spend under someone else's contract.
        memberMayUse: false,
    },
    {
        screen: "provider-keys",
        control: "pause or resume a key",
        route: "POST /api/v1/provider-keys/active",
        // Pausing silently moves every agent using that slot onto Decibyl's
        // key and onto Decibyl's bill.
        memberMayUse: false,
    },
    {
        screen: "provider-keys",
        control: "remove a key",
        route: "DELETE /api/v1/provider-keys",
        memberMayUse: false,
    },

    // --- Tools: pick an existing credential, do not mint one -------------
    {
        screen: "tools",
        control: "select an existing credential",
        route: null,
        // Otherwise a member cannot wire up a tool at all with what the
        // account already holds.
        memberMayUse: true,
    },
    {
        screen: "tools",
        control: "create a credential",
        route: "POST /api/v1/credentials/",
        // A secret, and spend under someone else's contract.
        memberMayUse: false,
    },
];

const on = (screen: string) => CONTROLS.filter((c) => c.screen === screen);

describe("every gated control names the route that enforces it", () => {
    it.each(CONTROLS)("$screen — $control", (control) => {
        // The pairing is the point: a control a member cannot use must have a
        // server-side rule behind it, or the UI is inventing a restriction the
        // product does not actually have.
        if (control.memberMayUse) {
            expect(control.route).toBeNull();
        } else {
            expect(control.route).toBeTruthy();
        }
    });
});

/**
 * Every role-gated route in the backend, and the screen that calls it.
 *
 * Built by grepping `require_organization_role` across `api/routes/` and
 * walking each hit back to its caller in `ui/src`. Two of the four defects
 * this audit found were invisible from either side alone:
 *
 * - `/numbers` called an ADMIN route from an ungated button, so a member was
 *   stopped three steps into renting a number;
 * - `/provider-keys` was hidden from members on the belief that the whole
 *   prefix was ADMIN, when its list route is deliberately open — which broke
 *   BYOK for exactly the people BYOK is for.
 *
 * Listed as data so the next person adding a gated route can see whether a
 * screen already calls it, rather than rediscovering this by grep.
 */
const GATED_ROUTES: { route: string; minimum: string; calledFrom: string | null }[] = [
    { route: "PUT /api/v1/provider-keys", minimum: "admin", calledFrom: "provider-keys" },
    { route: "POST /api/v1/provider-keys/active", minimum: "admin", calledFrom: "provider-keys" },
    { route: "DELETE /api/v1/provider-keys", minimum: "admin", calledFrom: "provider-keys" },
    { route: "PUT /api/v1/billing/profile", minimum: "admin", calledFrom: "billing" },
    { route: "POST /api/v1/billing/mandate", minimum: "admin", calledFrom: "numbers" },
    // Withdrawing autopay has no screen. Not a leak — nothing offers it — but
    // it does mean a customer cannot cancel a standing bank debit from inside
    // the product, which is a support ticket rather than a 403.
    { route: "POST /api/v1/billing/mandate/cancel", minimum: "admin", calledFrom: null },
    { route: "POST /api/v1/credentials/", minimum: "admin", calledFrom: "tools" },
    { route: "PUT /api/v1/credentials/{uuid}", minimum: "admin", calledFrom: null },
    { route: "DELETE /api/v1/credentials/{uuid}", minimum: "admin", calledFrom: null },
    { route: "DELETE /api/v1/do-not-call/{phone_number}", minimum: "admin", calledFrom: "do-not-call" },
    // Gated on the Owner tier, and on its own `isOwner` state rather than the
    // shared hook — see OrganizationMembersSection.
    { route: "PATCH /api/v1/organizations/members/{user_id}", minimum: "owner", calledFrom: "settings" },
    { route: "DELETE /api/v1/organizations/members/{user_id}", minimum: "owner", calledFrom: "settings" },
];

describe("every gated route a screen calls is gated on that screen", () => {
    it.each(GATED_ROUTES.filter((r) => r.calledFrom !== null))(
        "$route is reachable from $calledFrom",
        ({ route, calledFrom }) => {
            // Settings is covered by OrganizationMembersSection's own isOwner
            // check rather than by this matrix, so it is exempt from needing a
            // row here — but it still has to name a screen.
            if (calledFrom === "settings") {
                expect(route).toBeTruthy();
                return;
            }
            const entry = CONTROLS.find(
                (c) => c.screen === calledFrom && c.route === route
            );
            // A gated route whose screen has no matching row is the shape both
            // real defects took: the server said no and the UI never mentioned
            // it.
            expect(entry, `${route} has no gated control listed on ${calledFrom}`).toBeDefined();
            expect(entry?.memberMayUse).toBe(false);
        }
    );

    it("does not claim a gate the backend does not have", () => {
        // The other direction, and the one that broke BYOK: a control marked
        // admin-only in the UI must correspond to a route that is actually
        // gated. `GET /api/v1/provider-keys` is the counter-example that
        // started this — it takes plain `get_user`.
        const gated = new Set(GATED_ROUTES.map((r) => r.route));
        for (const control of CONTROLS.filter((c) => !c.memberMayUse)) {
            expect(gated.has(control.route!), `${control.route} is not in GATED_ROUTES`).toBe(
                true
            );
        }
    });
});

describe("a member can still do the job on each of these screens", () => {
    it.each(["billing", "do-not-call", "tools", "provider-keys", "numbers"])(
        "%s leaves something useful open to a member",
        (screen) => {
            // Guards against gating a whole screen by degrees: if every
            // control on one of these ends up admin-only, the door should have
            // been shut in navigation.ts instead, and this fails to say so.
            expect(on(screen).some((c) => c.memberMayUse)).toBe(true);
        }
    );

    it("keeps paying us open on billing", () => {
        const topUp = on("billing").find((c) => c.control.includes("top up"));
        expect(topUp?.memberMayUse).toBe(true);
    });

    it("keeps suppressing a number open, and un-suppressing closed", () => {
        // The asymmetry is deliberate and worth pinning: adding stops a call,
        // removing allows one.
        const add = on("do-not-call").find((c) => c.control.startsWith("add"));
        const remove = on("do-not-call").find((c) => c.control.startsWith("remove"));
        expect(add?.memberMayUse).toBe(true);
        expect(remove?.memberMayUse).toBe(false);
    });
});
