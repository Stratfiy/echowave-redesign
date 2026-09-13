/**
 * The bots in the rail.
 *
 * The rail is a shortcut, not the source of truth — /workflow lists every bot,
 * archive and folders included. So these tests are mostly about what the rail
 * must NOT do: bury the bot that needs attention, fill the screen on a large
 * account, or take the sidebar down with it when the roster request fails.
 */

import { render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const teamStatus = vi.hoisted(() => vi.fn());
const auth = vi.hoisted(() => ({ user: { id: 1 } as { id: number } | null, loading: false }));

vi.mock("next/navigation", () => ({ usePathname: () => "/overview" }));
vi.mock("@/lib/auth", () => ({ useAuth: () => auth }));
vi.mock("@/client/sdk.gen", () => ({ teamStatusApiV1TeamStatusGet: teamStatus }));
vi.mock("@/components/ui/sidebar", () => ({
    SidebarGroup: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    SidebarGroupLabel: ({ children }: { children: React.ReactNode }) => <div>{children}</div>,
    SidebarMenu: ({ children }: { children: React.ReactNode }) => <ul>{children}</ul>,
    SidebarMenuItem: ({ children }: { children: React.ReactNode }) => <li>{children}</li>,
    SidebarMenuButton: ({ children }: { children: React.ReactNode }) => <>{children}</>,
}));

import { RAIL_LIMIT, SidebarBots } from "../SidebarBots";

function member(id: number, name: string, tone = "working") {
    return {
        workflow_id: id, name, is_live: true, tone,
        status: `${id} calls`, at: null, calls: id, answered: id, outcomes: 0,
        failures: 0, last_action: null,
    };
}

beforeEach(() => {
    teamStatus.mockReset();
    auth.user = { id: 1 };
    auth.loading = false;
    teamStatus.mockResolvedValue({ data: { hours: 24, members: [member(1, "Front desk"), member(2, "Quote desk")] } });
});

describe("the bots in the rail", () => {
    it("lists them by name, linking each to its own screen", async () => {
        render(<SidebarBots collapsed={false} />);
        const link = await screen.findByRole("link", { name: /Front desk/ });
        expect(link.getAttribute("href")).toBe("/workflow/1");
        expect(screen.getByRole("link", { name: /Quote desk/ }).getAttribute("href")).toBe("/workflow/2");
    });

    it("keeps the server's worst-first order instead of re-sorting", async () => {
        teamStatus.mockResolvedValue({
            data: { hours: 24, members: [member(9, "Needs help", "attention"), member(1, "Aaa quiet", "idle")] },
        });
        render(<SidebarBots collapsed={false} />);
        await screen.findByRole("link", { name: /Needs help/ });
        const names = screen.getAllByRole("link").map((a) => a.textContent);
        // Alphabetical would put "Aaa quiet" first; the endpoint's order must win.
        expect(names[0]).toMatch(/Needs help/);
    });

    it("caps the list and says how many are not shown", async () => {
        const many = Array.from({ length: RAIL_LIMIT + 3 }, (_, i) => member(i + 1, `Bot ${i + 1}`));
        teamStatus.mockResolvedValue({ data: { hours: 24, members: many } });
        render(<SidebarBots collapsed={false} />);

        await screen.findByRole("link", { name: /Bot 1$/ });
        expect(screen.queryByRole("link", { name: new RegExp(`Bot ${RAIL_LIMIT + 1}$`) })).toBeNull();
        const more = screen.getByRole("link", { name: /3 more/ });
        expect(more.getAttribute("href")).toBe("/workflow");
    });

    it("renders nothing in icon mode, where a column of bare dots is a puzzle", () => {
        const { container } = render(<SidebarBots collapsed={true} />);
        expect(container.textContent).toBe("");
    });

    it("renders nothing on an account with no bots", async () => {
        teamStatus.mockResolvedValue({ data: { hours: 24, members: [] } });
        const { container } = render(<SidebarBots collapsed={false} />);
        await waitFor(() => expect(teamStatus).toHaveBeenCalled());
        expect(container.textContent).toBe("");
    });

    it("survives a failed roster request without throwing", async () => {
        teamStatus.mockRejectedValue(new Error("network"));
        const { container } = render(<SidebarBots collapsed={false} />);
        await waitFor(() => expect(teamStatus).toHaveBeenCalled());
        expect(container.textContent).toBe("");
    });
});

describe("it waits for auth", () => {
    /* The bug that shipped. The interceptor that attaches the token is
       registered only once auth has loaded, so a fetch sent before that is
       unauthenticated and fails quietly -- and this component renders nothing
       on a quiet failure, by design. On the loads where the fetch beat the
       interceptor the rail simply had no bots, and every test here passed
       because none of them said when the fetch was allowed to happen. */
    it("does not fetch while auth is still loading", () => {
        auth.loading = true;
        render(<SidebarBots collapsed={false} />);
        expect(teamStatus).not.toHaveBeenCalled();
    });

    it("does not fetch with no user", () => {
        auth.user = null;
        render(<SidebarBots collapsed={false} />);
        expect(teamStatus).not.toHaveBeenCalled();
    });

    it("fetches exactly once when auth is ready", async () => {
        render(<SidebarBots collapsed={false} />);
        await waitFor(() => expect(screen.getByText("Front desk")).toBeTruthy());
        expect(teamStatus).toHaveBeenCalledTimes(1);
    });
});
