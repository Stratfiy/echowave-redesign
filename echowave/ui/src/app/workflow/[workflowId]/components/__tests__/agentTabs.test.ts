/**
 * One row of tabs, and every destination named once.
 *
 * There were two rows. The agent-level strip carried Analysis and Advanced;
 * the settings page drew its own underneath with Analysis and Advanced again,
 * a centimetre apart and going to different places — and the outer "Advanced"
 * opened the inner "Calling". Nobody can be expected to guess that, and it is
 * the kind of thing that reads as fine to whoever built it and as broken to
 * everyone else.
 */

import { describe, expect, it } from "vitest";

import { TABS as SETTINGS_TABS } from "../../settings/tabs";
import { AGENT_TABS } from "../AgentTabs";

describe("the agent's tabs", () => {
    it("names each label exactly once", () => {
        const labels = AGENT_TABS.map((tab) => tab.label);
        expect(new Set(labels).size).toBe(labels.length);
    });

    it("sends each label to exactly one place", () => {
        const hrefs = AGENT_TABS.map((tab) =>
            "settingsTab" in tab ? `settings:${tab.settingsTab}` : tab.key,
        );
        expect(new Set(hrefs).size).toBe(hrefs.length);
    });

    it("offers every settings tab", () => {
        // The settings page no longer draws its own strip, so a tab missing
        // here is a screen nothing can reach.
        const reachable = AGENT_TABS.flatMap((tab) =>
            "settingsTab" in tab ? [tab.settingsTab as string] : [],
        );
        for (const tab of SETTINGS_TABS) {
            expect(reachable, `settings tab ${tab.id} is unreachable`).toContain(
                tab.id,
            );
        }
    });

    it("points at no settings tab that does not exist", () => {
        const known = SETTINGS_TABS.map((tab) => tab.id as string);
        for (const tab of AGENT_TABS) {
            if ("settingsTab" in tab) expect(known).toContain(tab.settingsTab);
        }
    });

    it("agrees with the settings page about what each tab is called", () => {
        // Two names for one screen is the same defect in a quieter form.
        const settingsLabels = new Map(
            SETTINGS_TABS.map((tab) => [tab.id as string, tab.label as string]),
        );
        for (const tab of AGENT_TABS) {
            if (!("settingsTab" in tab)) continue;
            expect(tab.label).toBe(settingsLabels.get(tab.settingsTab as string));
        }
    });
});
