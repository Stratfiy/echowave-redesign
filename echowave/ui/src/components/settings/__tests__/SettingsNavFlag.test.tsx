/** Daily brief (screen 20) is listed in Settings only while its flag is on. */
import { render, screen } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const flags: Record<string, boolean> = {};
vi.mock("@/lib/features", () => ({ useFeature: (name: string) => Boolean(flags[name]) }));
vi.mock("next/navigation", () => ({ usePathname: () => "/settings/team" }));

import { SettingsNav } from "../SettingsNav";

describe("Settings: Daily brief", () => {
    beforeEach(() => {
        delete flags.daily_brief;
    });

    it("is hidden while the brief is off", () => {
        render(<SettingsNav />);
        expect(screen.queryAllByRole("link", { name: "Daily brief" })).toHaveLength(0);
        expect(screen.getAllByRole("link", { name: "Team" }).length).toBeGreaterThan(0);
    });

    it("appears under You while it is on", () => {
        flags.daily_brief = true;
        render(<SettingsNav />);
        const links = screen.getAllByRole("link", { name: "Daily brief" });
        expect(links[0].getAttribute("href")).toBe("/settings/daily-brief");
    });
});
