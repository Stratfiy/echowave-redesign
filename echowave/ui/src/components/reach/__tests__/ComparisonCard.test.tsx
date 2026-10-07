/**
 * The comparison (stream `reach`) names which apps were compared, which
 * were not and why, and when -- and says listed prices are not the total.
 */

import { render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it } from "vitest";

import { ComparisonCard } from "../ComparisonCard";

const event = {
    id: 9,
    at: "2026-10-08T08:35:00Z",
    kind: "reach_comparison",
    actor: "agent",
    summary: "Compared Zomato for 2 items",
    payload: {
        items: ["paneer tikka", "toned milk"],
        compared: [
            {
                app: "Zomato",
                lines: [
                    { asked: "paneer tikka", found: "Paneer Tikka", store: "Spice Route", price_paise: 22000 },
                    { asked: "toned milk", found: null, store: null, price_paise: null },
                ],
                listed_total_paise: 22000,
                missing: ["toned milk"],
                coupons: [{ code: "SAVE50", description: "₹50 off above ₹200" }],
            },
        ],
        not_compared: [{ app: "Swiggy", why: "Swiggy needs Builders Club access." }],
        at: "2026-10-08T08:35:00Z",
    },
    is_deliverable: false,
    workflow_id: null,
    workflow_run_id: null,
    folder_id: null,
} as never;

describe("the comparison", () => {
    it("says which apps, which not and why, and when", () => {
        render(<ComparisonCard event={event} />);
        const text = screen.getByTestId("comparison-card").textContent ?? "";
        expect(text).toContain("Compared Zomato");
        expect(text).toContain("Zomato: ₹220 listed");
        expect(text).toContain("not found: toned milk");
        expect(text).toContain("SAVE50");
        expect(text).toContain("Not compared: Swiggy (Swiggy needs Builders Club access.)");
        expect(text).toContain("the figure that counts");
        expect(document.querySelector("time")?.getAttribute("datetime")).toBe("2026-10-08T08:35:00Z");
    });
});
