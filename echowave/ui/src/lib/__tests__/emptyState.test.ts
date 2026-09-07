/**
 * Nothing to show, for one of two very different reasons.
 */

import { describe, expect, it } from "vitest";

import { emptyReason } from "@/lib/emptyState";

describe("why a list is empty", () => {
    it("is the account's first visit when no filter is applied", () => {
        expect(emptyReason({ hasFilters: false })).toBe("no-data");
    });

    it("is the filter when one is applied", () => {
        // Otherwise an account with ten thousand calls is told it has none,
        // which is the version of this bug that costs trust rather than time.
        expect(emptyReason({ hasFilters: true })).toBe("filtered-out");
    });
});
