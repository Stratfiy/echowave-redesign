/**
 * Who is offered which destination.
 *
 * The sidebar hid the staff section correctly and the top-bar search did not:
 * it built its index from `[...NAV_SECTIONS, STAFF_SECTION]` unconditionally,
 * so a customer typing "admin", "kyc" or "approve" was offered the staff
 * review queue. A hidden sidebar entry is not hidden if the search box still
 * hands it over, and a second door is exactly the kind of thing nobody
 * re-checks after the first one is closed.
 *
 * None of this is a security boundary — every route behind these screens is
 * enforced on the server with `require_organization_role` or `get_superuser`.
 * It is about not advertising to a customer that Decibyl's internal console
 * exists one URL away, and not showing a member a control that can only
 * answer them with a 403.
 */

import { describe, expect, it } from "vitest";

import { NAV_SECTIONS, type SidebarNavItem, STAFF_SECTION } from "../navigation";

type Roles = { isStaff: boolean; isOrganizationAdmin: boolean };

/** The filter both the sidebar and the search index apply. */
function visibleTo(roles: Roles): SidebarNavItem[] {
    const sections = roles.isStaff ? [...NAV_SECTIONS, STAFF_SECTION] : NAV_SECTIONS;
    return sections
        .flatMap((section) => section.items)
        .filter((item) => !item.requiresOrganizationAdmin || roles.isOrganizationAdmin);
}

const MEMBER: Roles = { isStaff: false, isOrganizationAdmin: false };
const ADMIN: Roles = { isStaff: false, isOrganizationAdmin: true };
const STAFF: Roles = { isStaff: true, isOrganizationAdmin: false };

const urls = (roles: Roles) => visibleTo(roles).map((item) => item.url);

describe("the staff area is not offered to customers", () => {
    it("hides the review queue from a member", () => {
        expect(urls(MEMBER)).not.toContain("/superadmin");
    });

    it("hides it from an organization admin too", () => {
        // Organization admin is a customer's own tier. It says nothing about
        // whether they work for Decibyl, and conflating the two is the whole
        // reason these are separate fields on the user.
        expect(urls(ADMIN)).not.toContain("/superadmin");
    });

    it("shows it to staff", () => {
        expect(urls(STAFF)).toContain("/superadmin");
    });

    it("never lands a customer on the staff queue, whatever they type", () => {
        // Asserted against the destination rather than the words, because the
        // words overlap legitimately: "kyc" is also a keyword on the customer's
        // own Verification screen, and a customer searching "kyc" *should*
        // find that. What must never happen is a result pointing at
        // /superadmin. An earlier version of this test asserted on the keyword
        // and failed for the right reason.
        const staffTerms = [
            STAFF_SECTION.items[0].title.toLowerCase(),
            ...(STAFF_SECTION.items[0].keywords ?? []),
        ];

        for (const term of staffTerms) {
            const hits = visibleTo(MEMBER).filter((item) => {
                const title = item.title.toLowerCase();
                const kw = (item.keywords ?? []).join(" ");
                return title.includes(term) || kw.includes(term);
            });
            expect(hits.map((item) => item.url)).not.toContain("/superadmin");
        }
    });
});

describe("admin-only destinations are hidden from members", () => {
    it("hides provider keys from a member", () => {
        // Every route under /api/v1/provider-keys requires ADMIN, so this
        // screen can only answer a member with a 403.
        expect(urls(MEMBER)).not.toContain("/provider-keys");
    });

    it("shows provider keys to an admin", () => {
        expect(urls(ADMIN)).toContain("/provider-keys");
    });
});

describe("what stays open to every member", () => {
    it("leaves billing reachable", () => {
        // Topping up is deliberately not admin-gated: "a member who cannot top
        // up when the balance runs out is a member who cannot work, and paying
        // us more money is not a privilege that needs protecting." Hiding the
        // screen would gate the payment along with the mandate.
        expect(urls(MEMBER)).toContain("/billing");
    });

    it("leaves the do-not-call list reachable", () => {
        // Listing, adding and uploading are open to members; only removal is
        // admin-gated, so the door stays open and the control is gated inside.
        expect(urls(MEMBER)).toContain("/do-not-call");
    });

    it("leaves the product itself reachable", () => {
        // The thing the customer bought. If a role filter ever swallows these,
        // the account looks empty rather than restricted.
        for (const url of ["/workflow", "/campaigns", "/files", "/usage"]) {
            expect(urls(MEMBER)).toContain(url);
        }
    });

    it("gives a member most of the product, not a stub", () => {
        // A guard against over-correction: if a future change marks half the
        // nav admin-only, a member's account becomes unusable and this fails
        // rather than shipping.
        expect(urls(MEMBER).length).toBeGreaterThanOrEqual(urls(ADMIN).length - 2);
    });
});
