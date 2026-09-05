/**
 * Every setting has exactly one home.
 *
 * Splitting nine cards across five tabs is the kind of change where a section
 * goes missing silently: it still compiles, the page still renders, and the
 * only symptom is a customer who cannot find voicemail detection any more. A
 * census by eye catches that once; this catches it every time.
 *
 * SECTION_IDS is the list of anchors the page actually renders. Adding a card
 * without giving it a tab, or giving one to two tabs, fails here.
 */

import { describe, expect, it } from "vitest";

import { isTabId, TABS } from "../tabs";

/** Every `id` rendered on the settings page, from its Card anchors. */
const SECTION_IDS = [
    "qa",
    "general",
    "models",
    "variables",
    "dictionary",
    "voicemail",
    "recordings",
    "deployment",
    "report",
    "identity",
];

describe("settings tabs", () => {
    it("gives every section exactly one tab", () => {
        const placed = TABS.flatMap((tab) => tab.sections as readonly string[]);
        for (const id of SECTION_IDS) {
            expect(
                placed.filter((s) => s === id),
                `section ${id} should appear in exactly one tab`,
            ).toHaveLength(1);
        }
    });

    it("places no section that the page does not render", () => {
        for (const tab of TABS) {
            for (const section of tab.sections) {
                expect(SECTION_IDS, `tab ${tab.id} names an unknown section`).toContain(
                    section,
                );
            }
        }
    });

    it("has a distinct label and id per tab", () => {
        expect(new Set(TABS.map((t) => t.id)).size).toBe(TABS.length);
        expect(new Set(TABS.map((t) => t.label)).size).toBe(TABS.length);
    });

    it("accepts only real tab ids from the URL", () => {
        expect(isTabId("models")).toBe(true);
        expect(isTabId("calling")).toBe(true);
        // A stale or hand-typed ?tab= must fall back rather than render nothing.
        expect(isTabId("general")).toBe(false);
        expect(isTabId("")).toBe(false);
        expect(isTabId(null)).toBe(false);
    });
});
