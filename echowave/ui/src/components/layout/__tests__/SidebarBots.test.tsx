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
const route = vi.hoisted(() => ({ pathname: "/overview" }));
const auth = vi.hoisted(() => ({ user: { id: 1 } as { id: number } | null, loading: false }));

vi.mock("next/navigation", () => ({ usePathname: () => route.pathname }));
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
    route.pathname = "/overview";
    teamStatus.mockReset();
    auth.user = { id: 1 };
    auth.loading = false;
    teamStatus.mockResolvedValue({ data: { hours: 24, members: [member(1, "Front desk"), member(2, "Quote desk")] } });
});

describe("the agents in the rail", () => {
    it.each(["/workflow/1", "/workflow/1/settings", "/workflow/10/thread"])("does not select a DM on %s", async (path) => {
        route.pathname = path;
        render(<SidebarBots collapsed={false} />);
        expect((await screen.findByRole("link", { name: /Front desk/ })).getAttribute("aria-current")).toBeNull();
    });
    it("selects the matching DM thread", async () => {
        route.pathname = "/workflow/1/thread";
        render(<SidebarBots collapsed={false} />);
        expect((await screen.findByRole("link", { name: /Front desk/ })).getAttribute("aria-current")).toBe("page");
        expect(screen.getByRole("link", { name: /Quote desk/ }).getAttribute("aria-current")).toBeNull();
    });
    it("lists them by name, each opening its thread", async () => {
        // The thread, not the editor. A bot in the workspace panel is a
        // teammate you talk to; how it is configured is a tab away once you
        // are there. Landing on the model form was what made the rail read as
        // a builder rather than a team.
        render(<SidebarBots collapsed={false} />);
        const link = await screen.findByRole("link", { name: /Front desk/ });
        expect(link.getAttribute("href")).toBe("/workflow/1/thread");
        expect(screen.getByRole("link", { name: /Quote desk/ }).getAttribute("href")).toBe("/workflow/2/thread");
    });

    it("keeps the server's worst-first order instead of re-sorting", async () => {
        teamStatus.mockResolvedValue({
            data: { hours: 24, members: [member(9, "Needs help", "attention"), member(1, "Aaa quiet", "idle")] },
        });
        render(<SidebarBots collapsed={false} />);
        await screen.findByRole("link", { name: /Needs help/ });
        // Bot rows only: the section heading and its plus are links too, and
        // they come first in the DOM by design.
        const names = screen
            .getAllByRole("link")
            .filter((a) => /^\/workflow\/\d+\/thread$/.test(a.getAttribute("href") ?? ""))
            .map((a) => a.textContent);
        // Alphabetical would put "Aaa quiet" first; the endpoint's order must win.
        expect(names[0]).toMatch(/Needs help/);
    });

    it("caps the list and says how many are not shown", async () => {
        const many = Array.from({ length: RAIL_LIMIT + 3 }, (_, i) => member(i + 1, `Agent ${i + 1}`));
        teamStatus.mockResolvedValue({ data: { hours: 24, members: many } });
        render(<SidebarBots collapsed={false} />);

        await screen.findByRole("link", { name: /Agent 1$/ });
        expect(screen.queryByRole("link", { name: new RegExp(`Agent ${RAIL_LIMIT + 1}$`) })).toBeNull();
        const more = screen.getByRole("link", { name: /3 more/ });
        expect(more.getAttribute("href")).toBe("/workflow");
    });

    it("renders nothing in icon mode, where a column of bare dots is a puzzle", () => {
        const { container } = render(<SidebarBots collapsed={true} />);
        expect(container.textContent).toBe("");
    });

    it("still offers to hire on an account with no agents", async () => {
        // The heading and its plus stay: a fresh account needs the door to
        // its first bot more than a full one needs the ninth row.
        teamStatus.mockResolvedValue({ data: { hours: 24, members: [] } });
        render(<SidebarBots collapsed={false} />);
        await waitFor(() => expect(teamStatus).toHaveBeenCalled());
        expect(screen.getByLabelText("Add an agent").getAttribute("href")).toBe("/start");
        expect(screen.queryByRole("link", { name: /Front desk/ })).toBeNull();
    });

    it("survives a failed roster request without throwing", async () => {
        teamStatus.mockRejectedValue(new Error("network"));
        render(<SidebarBots collapsed={false} />);
        await waitFor(() => expect(teamStatus).toHaveBeenCalled());
        // No rows and no error; the door is still there.
        expect(screen.getByLabelText("Add an agent")).toBeTruthy();
        expect(screen.queryByRole("link", { name: /Front desk/ })).toBeNull();
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

describe("the last line and the unread dot", () => {
    it("shows the name and nothing else", async () => {
        // A second line of the last thing said doubled the height of every
        // row and read as noise in a list somebody scans to find a bot. The
        // dot beside the name already says there is something new.
        teamStatus.mockResolvedValue({
            data: {
                hours: 24,
                members: [
                    { ...member(1, "Front desk"), last_line: "Booked Meera for 4pm.", last_at: "2026-09-14T06:30:00Z", last_actor: "agent" },
                    { ...member(2, "Quote desk"), last_line: "Can you quote Chennai?", last_at: "2026-09-14T06:00:00Z", last_actor: "human" },
                ],
            },
        });
        render(<SidebarBots collapsed={false} />);
        await waitFor(() => expect(screen.getByText("Front desk")).toBeTruthy());
        expect(screen.queryByText("Booked Meera for 4pm.")).toBeNull();
        expect(screen.queryByText(/You: Can you quote Chennai\?/)).toBeNull();
    });

    it("names the list without making the heading a second door", async () => {
        // Agents is pinned above and goes to the roster; this heading said
        // the same thing on the same panel. Buzz's Direct messages is a
        // label, not a link.
        teamStatus.mockResolvedValue({ data: { hours: 24, members: [member(1, "Front desk")] } });
        render(<SidebarBots collapsed={false} />);
        await waitFor(() => expect(screen.getByText("Direct messages")).toBeTruthy());
        expect(screen.queryByRole("link", { name: "Direct messages" })).toBeNull();
        // The plus stays: it is the door a fresh account needs.
        expect(screen.getByLabelText("Add an agent")).toBeTruthy();
    });

    it("lights the dot for an agent with news since it was last opened here", async () => {
        localStorage.setItem(
            "decibyl.bot-seen",
            JSON.stringify({ "1": "2026-09-14T07:00:00Z", "2": "2026-09-14T05:00:00Z" }),
        );
        teamStatus.mockResolvedValue({
            data: {
                hours: 24,
                members: [
                    { ...member(1, "Read"), last_line: "old", last_at: "2026-09-14T06:30:00Z" },
                    { ...member(2, "Unread"), last_line: "new", last_at: "2026-09-14T06:00:00Z" },
                    { ...member(3, "Never opened"), last_line: "first", last_at: "2026-09-14T06:00:00Z" },
                    { ...member(4, "Silent"), last_line: null, last_at: null },
                ],
            },
        });
        render(<SidebarBots collapsed={false} />);
        await waitFor(() => expect(screen.getByText("Unread")).toBeTruthy());
        const dots = screen.getAllByLabelText("Unread").filter((el) => el.tagName === "SPAN");
        // Unread and Never opened; not Read, not a bot with nothing to say.
        expect(dots.length).toBe(2);
        localStorage.clear();
    });
});
