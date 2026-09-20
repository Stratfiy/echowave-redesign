import { fireEvent, render, screen } from "@testing-library/react";
import React from "react";
import { describe, expect, it, vi } from "vitest";

import { AgentTabs } from "../AgentTabs";

const route = vi.hoisted(() => ({ pathname: "/workflow/39" }));
vi.mock("next/navigation", () => ({ usePathname: () => route.pathname }));

function openSetup() {
    const trigger = screen.getByRole("button", { name: /Setup/ });
    trigger.focus();
    fireEvent.keyDown(trigger, { key: "Enter" });
}

describe("agent navigation", () => {
    it("keeps messaging separate and exposes just two primary links", () => {
        route.pathname = "/workflow/39";
        render(<AgentTabs workflowId={39} />);
        expect(screen.getAllByRole("link").map((link) => link.textContent)).toEqual(["Edit", "Activity", "Message"]);
        expect(screen.getByRole("link", { name: "Message" }).getAttribute("href")).toBe("/workflow/39/thread");
        expect(screen.getByRole("link", { name: "Edit" }).getAttribute("aria-current")).toBe("page");
        expect(screen.queryByText("Tools")).toBeNull();
    });

    it("retains nested run highlighting without highlighting the editor", () => {
        route.pathname = "/workflow/39/runs/123";
        render(<AgentTabs workflowId={39} />);
        expect(screen.getByRole("link", { name: "Activity" }).getAttribute("aria-current")).toBe("page");
        expect(screen.getByRole("link", { name: "Edit" }).getAttribute("aria-current")).toBeNull();
    });

    it("opens setup by keyboard and preserves every contextual destination", async () => {
        route.pathname = "/workflow/39/settings";
        render(<AgentTabs workflowId={39} settingsTab="analysis" dirtyTabs={new Set(["advanced"])} />);
        expect(screen.getByRole("button", { name: /Setup · Quality/ })).toBeTruthy();
        expect(screen.getAllByLabelText("Unsaved changes")).toHaveLength(1);
        openSetup();
        const quality = await screen.findByRole("menuitem", { name: "Quality" });
        expect(quality.getAttribute("href")).toBe("/workflow/39/settings?tab=analysis");
        expect(quality.getAttribute("aria-current")).toBe("page");
        for (const [name, path] of [["Tools", "tools"], ["Triggers", "triggers"], ["Advanced", "settings?tab=advanced"], ["Share", "settings?tab=share"], ["Analytics", "analytics"]]) {
            expect(screen.getByRole("menuitem", { name: new RegExp(name) }).getAttribute("href")).toBe(`/workflow/39/${path}`);
        }
        expect(screen.queryByRole("menuitem", { name: "Message" })).toBeNull();
        expect(screen.getAllByLabelText("Unsaved changes")).toHaveLength(2);
    });
});
