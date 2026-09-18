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
import { TELEPHONY_TABS } from "../SectionTabs";

const current = () =>
    screen
        .getAllByRole("link")
        .find((el) => el.getAttribute("aria-current") === "page")
        ?.textContent;

describe("the section strip", () => {
    it("lights the tab you are on", () => {
        path.value = "/telephony-configurations";
        render(<PageTabs tabs={TELEPHONY_TABS} />);
        expect(current()).toBe("Your numbers");
    });

    it("lights Get a number while you are doing its verification step", () => {
        path.value = "/verification";
        render(<PageTabs tabs={TELEPHONY_TABS} />);
        expect(current()).toBe("Get a number");
    });
});
