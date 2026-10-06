/**
 * A screen that is a step inside another one has no tab of its own, and the
 * strip has to keep saying where the reader is standing. Verification is
 * step 1 of getting a number; landing on it used to light nothing at all
 * once its own tab came off the strip.
 */

import { render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it, vi } from "vitest";

const path = vi.hoisted(() => ({ value: "/numbers" }));
vi.mock("next/navigation", () => ({ usePathname: () => path.value }));

import { PageTabs } from "../PageHeader";
import { BILLING_TABS, TELEPHONY_TABS } from "../SectionTabs";

const current = () =>
    screen
        .getAllByRole("link")
        .find((el) => el.getAttribute("aria-current") === "page")
        ?.textContent;

describe("the section strip", () => {
    it("lights the tab you are on", () => {
        path.value = "/settings/phone-number";
        render(<PageTabs tabs={TELEPHONY_TABS} />);
        expect(current()).toBe("Your numbers");
    });

    it("lights a tab for a route it names in `also`", () => {
        // For a screen that is a step inside another, with no tab of its own.
        path.value = "/step";
        render(<PageTabs tabs={[{ href: "/whole", label: "Whole", also: ["/step"] }, { href: "/other", label: "Other" }]} />);
        expect(current()).toBe("Whole");
    });

    it("lights one tab, the most specific, when two match", () => {
        // /billing carries prefix, so /billing/spend matches Billing as well.
        path.value = "/billing/spend";
        render(<PageTabs tabs={BILLING_TABS} />);
        expect(current()).toBe("Spend");
        expect(screen.getAllByRole("link").filter((el) => el.getAttribute("aria-current") === "page")).toHaveLength(1);
    });
});
