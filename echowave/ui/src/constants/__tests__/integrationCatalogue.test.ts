/**
 * The catalogue's one rule: nothing on it is a promise we cannot keep.
 *
 * A page like this is the answer to the first question a business asks — will
 * it talk to what I already run on — and a card that leads nowhere is worse
 * than no card, because they find out after signing rather than before.
 */

import { existsSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

import {
    CATALOGUE,
    catalogueByCategory,
    CATEGORY_ORDER,
    CONNECT_HINTS,
    CONNECT_LABELS,
    searchCatalogue,
} from "../integrationCatalogue";

describe("every card leads somewhere", () => {
    it.each(CATALOGUE.filter((e) => e.connect !== "request").map((e) => [e.id, e]))(
        "%s has a destination",
        (_id, entry) => {
            expect(entry.href, `${entry.name} claims to be connectable`).toBeTruthy();
        },
    );

    it("the ones with no destination say so rather than looking available", () => {
        for (const entry of CATALOGUE.filter((e) => !e.href)) {
            expect(entry.connect, `${entry.name} has no href`).toBe("request");
        }
    });

    it("every destination is a route in this product, not an outside link", () => {
        for (const entry of CATALOGUE) {
            if (entry.href) expect(entry.href.startsWith("/")).toBe(true);
        }
    });

    it("every destination is a page that exists", () => {
        // The failure this page must never have. A card pointing at a route
        // somebody has since renamed looks identical to a working one until
        // it is clicked, and the person clicking it is deciding whether to
        // buy.
        for (const entry of CATALOGUE) {
            if (!entry.href) continue;
            // An href may deep-link to a card on the page (#google-calendar).
            // Only the path part names a route; keeping the fragment here
            // would look for a directory called "integrations#google-calendar".
            const path = entry.href.split("#")[0];
            const route = join(process.cwd(), "src/app", path, "page.tsx");
            expect(existsSync(route), `${entry.name} -> ${entry.href}`).toBe(true);
        }
    });
});

describe("the shape of it", () => {
    it("has no duplicate ids", () => {
        const ids = CATALOGUE.map((e) => e.id);
        expect(new Set(ids).size).toBe(ids.length);
    });

    it("has no duplicate names", () => {
        const names = CATALOGUE.map((e) => e.name);
        expect(new Set(names).size).toBe(names.length);
    });

    it("puts every card in a category the page renders", () => {
        for (const entry of CATALOGUE) {
            expect(CATEGORY_ORDER, `${entry.name}`).toContain(entry.category);
        }
    });

    it("loses nothing when grouped", () => {
        const grouped = catalogueByCategory().flatMap((g) => g.entries);
        expect(grouped).toHaveLength(CATALOGUE.length);
    });

    it("labels and explains every way of connecting", () => {
        for (const entry of CATALOGUE) {
            expect(CONNECT_LABELS[entry.connect]).toBeTruthy();
            expect(CONNECT_HINTS[entry.connect]).toBeTruthy();
        }
    });

    it("gives every card something that says what it does for you", () => {
        for (const entry of CATALOGUE) {
            expect(entry.blurb.length, entry.name).toBeGreaterThan(10);
        }
    });
});

describe("finding one", () => {
    it("matches on the name", () => {
        expect(searchCatalogue("zoho").map((e) => e.id)).toEqual(["zoho"]);
    });

    it("matches on what it does, because that is what people type", () => {
        // Somebody looking for "appointment" does not know we call it Calendar.
        expect(searchCatalogue("appointment").length).toBeGreaterThan(0);
    });

    it("ignores case and surrounding space", () => {
        expect(searchCatalogue("  HubSpot ").map((e) => e.id)).toEqual(["hubspot"]);
    });

    it("an empty query shows everything rather than nothing", () => {
        expect(searchCatalogue("   ")).toHaveLength(CATALOGUE.length);
    });
});
