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

describe("editing and activity, with separate messaging", () => {
    it("prioritizes editing and activity", () => {
        const labels = AGENT_TABS.map((tab) => tab.label);
        expect(labels.slice(0, 2)).toEqual(["Edit", "Activity"]);
    });

    it("uses a distinct messaging action and activity destination", () => {
        const labels = AGENT_TABS.map((tab) => tab.label);
        expect(labels).toContain("Message");
        expect(labels).toContain("Activity");
        expect(labels).not.toContain("Chat");
        expect(labels).not.toContain("Logs");
    });

    it("does not offer the graph as a tab of its own", () => {
        // The canvas is how the instructions are drawn, not a thing the bot
        // has beside Logs and Tools. It is still a linkable place
        // (?view=graph) and Instructions carries the button to it.
        expect(AGENT_TABS.map((tab) => tab.label as string)).not.toContain("Graph");
    });
});


describe("reporting and configuring are different tabs", () => {
    it("offers Analytics, and does not call anything else Analysis", () => {
        // Two words one letter apart, a centimetre apart, for a screen that
        // reports what happened and a screen that configures how calls are
        // judged. The second is Quality.
        const labels = AGENT_TABS.map((tab) => tab.label as string);
        expect(labels).toContain("Analytics");
        expect(labels).not.toContain("Analysis");
    });

    it("keeps the settings tab's id so existing links still land", () => {
        // ?tab=analysis is in links and bookmarks. Renaming the label is a
        // rename; renaming the id would be a broken link.
        const quality = AGENT_TABS.find((tab) => tab.label === "Quality");
        expect(quality && "settingsTab" in quality && quality.settingsTab).toBe(
            "analysis",
        );
    });
});
