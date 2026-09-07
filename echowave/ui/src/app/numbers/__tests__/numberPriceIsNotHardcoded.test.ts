/**
 * The buy-a-number screen must not carry its own price.
 *
 * It did, in three places, and the figure was ₹349 while every account was
 * actually charged `NUMBER_RENTAL_PRICE_PAISE` — ₹559 for months. The customer
 * read ₹349, authorised a standing instruction, and was billed ₹559. The screen
 * even contradicted itself: the authorised-mandate line below rendered the real
 * `price_paise` from the API, so the two figures sat a few hundred pixels apart.
 *
 * A quote shown before somebody authorises a recurring charge has to come from
 * the thing that bills them, so the price is now served on `GET /billing/mandate`
 * by the same `next_number_price_paise` resolver the charge uses.
 *
 * This reads the source rather than rendering, deliberately: the bug was a
 * literal in JSX copy, and a literal is what this has to catch. Rendering the
 * page would need the whole fetch/mandate/step machinery stubbed, and a stub
 * that returns a price cannot fail the way the original did.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const SOURCE = readFileSync(join(__dirname, "..", "page.tsx"), "utf8");

/** A rupee sign followed by digits — the shape of a hardcoded price. */
const RUPEE_LITERAL = /₹\s?\d/g;

describe("the buy-a-number screen", () => {
    it("quotes no price of its own", () => {
        // Comments explain the bug and name the old figures; they ship nothing
        // to the browser, so they are not what this guards against.
        const code = SOURCE.replace(/\/\/[^\n]*/g, "").replace(
            /\/\*[\s\S]*?\*\//g,
            "",
        );
        expect(code.match(RUPEE_LITERAL) ?? []).toEqual([]);
    });

    it("reads the price the API serves", () => {
        expect(SOURCE).toContain("number_price_paise");
        expect(SOURCE).toContain("formatPaise(");
    });
});
