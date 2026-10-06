/**
 * The navigation: every destination is on the rail or in its account menu.
 *
 * Before it, ten screens were reachable only from the profile menu. The test
 * that matters is the first: a new nav item placed nowhere fails here, rather
 * than shipping as a screen nobody can find -- the silent-absence shape,
 * applied to navigation.
 */
import { describe, expect, it } from "vitest";

import { SETTINGS_SECTIONS } from "../../settings/sections";
import { getVisibleNavSections, SHELL_MANAGE, shellUrls, STAFF_SECTION, visibleShellManage } from "../navigation";

const ALL = { isStaff: true, isOrganizationAdmin: true, isSuperadmin: true };
const MEMBER = { isStaff: false, isOrganizationAdmin: false };

describe("the navigation", () => {
    it("puts every non-staff destination on the rail or in the account menu", () => {
        const staff = new Set(STAFF_SECTION.items.map((i) => i.url));
        const onSidebar = new Set(shellUrls());
        const missing = getVisibleNavSections(ALL)
            .flatMap((s) => s.items)
            .filter((i) => !staff.has(i.url) && !onSidebar.has(i.url))
            .map((i) => `${i.title} (${i.url})`);
        expect(missing).toEqual([]);
    });

    it("never lists one destination twice", () => {
        const urls = shellUrls();
        expect(new Set(urls).size).toBe(urls.length);
    });

    it("points only at destinations that exist", () => {
        // Settings' own sections and Studio have no legacy nav item.
        const known = new Set([
            ...getVisibleNavSections(ALL).flatMap((s) => s.items.map((i) => i.url)),
            ...SETTINGS_SECTIONS.map((section) => section.href),
            "/studio",
        ]);
        for (const url of shellUrls()) expect(known.has(url), url).toBe(true);
    });

    it("keeps the account menu to what Settings does not hold", () => {
        expect(SHELL_MANAGE.map((e) => e.title)).toEqual(["Marketplace", "Deploy", "Billing"]);
    });

    it("reaches every page the rail used to hold, through Settings", () => {
        const urls = new Set(shellUrls());
        for (const url of ["/settings/company", "/settings/knowledge", "/settings/channels", "/settings/team", "/settings"]) {
            expect(urls.has(url), url).toBe(true);
        }
    });

    it("shows a member the whole of it -- nothing here is admin-only today", () => {
        expect(visibleShellManage(getVisibleNavSections(MEMBER))).toEqual(SHELL_MANAGE);
    });

    it("drops a page a role hides, and a group left with nothing", () => {
        const sections = [{ items: [{ title: "Billing", url: "/billing", icon: SHELL_MANAGE[0].icon }] }];
        expect(visibleShellManage(sections).map((e) => e.title)).toEqual(["Billing"]);
    });
});
