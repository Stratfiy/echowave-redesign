import { readdirSync, readFileSync, statSync } from "node:fs";
import { join, relative } from "node:path";

import { describe, expect, it } from "vitest";

import { DESTINATIONS } from "../nav";

/**
 * Phase 3: every staff screen is reachable from the staff navigation. A
 * screen nobody can get to counts as missing, so this walks every page under
 * app/superadmin and requires each one to be a navigation link, or to be
 * linked from a named screen that is itself reachable (and that file must
 * really contain the link). Detail pages ([id]) are reached from their lists.
 */

const ROOT = join(__dirname, "../../../app/superadmin");

function pages(dir: string): string[] {
    const out: string[] = [];
    for (const name of readdirSync(dir)) {
        const full = join(dir, name);
        if (statSync(full).isDirectory()) out.push(...pages(full));
        else if (name === "page.tsx") out.push(full);
    }
    return out;
}

function hrefOf(file: string): string {
    const rel = relative(ROOT, file).replace(/\\/g, "/").replace(/\/?page\.tsx$/, "");
    return rel ? `/superadmin/${rel}` : "/superadmin";
}

/** Screens linked from another screen rather than the rail: href -> the file that links it. */
const IN_PAGE: Record<string, string> = {
    "/superadmin/audit": "app/superadmin/SuperadminNav.tsx",
    "/superadmin/support/actions/new": "components/support/staff/CaseContext.tsx",
    "/superadmin/telephony/shared-outbound": "app/superadmin/telephony/page.tsx",
    "/superadmin/telephony/demo-agent": "app/superadmin/telephony/page.tsx",
    "/superadmin/controls": "lib/staff/nav.ts",
};

describe("staff screens are reachable", () => {
    const navHrefs = new Set(DESTINATIONS.flatMap((d) => [d.href, ...d.children.filter((c) => !c.needsSetup).map((c) => c.href)]));
    const billingTabs = readFileSync(join(ROOT, "billing/layout.tsx"), "utf8");
    const all = pages(ROOT)
        .map(hrefOf)
        .filter((href) => !href.includes("["));

    it.each(all)("%s is linked", (href) => {
        if (navHrefs.has(href)) return;
        if (href.startsWith("/superadmin/billing/") && billingTabs.includes(`"${href}"`)) return;
        const linker = IN_PAGE[href];
        expect(linker, `${href} is reachable from nothing: add it to the staff navigation`).toBeDefined();
        const source = readFileSync(join(__dirname, "../../..", linker!), "utf8");
        expect(source.includes(href), `${linker} does not link ${href}`).toBe(true);
    });
});
