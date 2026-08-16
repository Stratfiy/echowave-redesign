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

describe("a member can still do the job on each of these screens", () => {
    it.each(["billing", "do-not-call", "tools"])(
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
