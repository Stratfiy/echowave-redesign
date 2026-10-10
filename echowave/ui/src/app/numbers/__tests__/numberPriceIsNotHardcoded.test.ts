/**
 * The get-a-number screen shows no price and asks for no autopay.
 *
 * It used to quote a monthly rental, and the figure drifted from what was
 * billed (Rs349 on screen, Rs559 charged). The founder then decided that no
 * pricing is shown to users (9 Oct 2026), and there is no checkout, so there
 * is nothing to quote and no standing instruction to authorise. What limits a
 * free number is a per-account cap, enforced on the server.
 *
 * This reads the source rather than rendering, deliberately: the bug was a
 * literal in JSX copy, and a literal is what this has to catch.
 */

import { readFileSync } from "node:fs";
import { join } from "node:path";

import { describe, expect, it } from "vitest";

const SOURCE = readFileSync(join(__dirname, "..", "page.tsx"), "utf8");

// Comments explain history and ship nothing to the browser.
const CODE = SOURCE.replace(/\/\/[^\n]*/g, "").replace(/\/\*[\s\S]*?\*\//g, "");

describe("the get-a-number screen", () => {
    it("quotes no price", () => {
        expect(CODE.match(/₹\s?\d/g) ?? []).toEqual([]);
        expect(CODE).not.toMatch(/number_price_paise|monthly_rental|setup_price|formatPaise/);
        expect(CODE).not.toMatch(/a month|\/month|per month/i);
    });

    it("asks for no autopay and calls no billing route", () => {
        expect(CODE).not.toMatch(/autopay|mandate/i);
        expect(CODE).not.toMatch(/Billing/);
    });
});
