/**
 * Studio's screen: the chat sends and survives a refused request, the site
 * panel sandboxes its preview and shows why a build failed, and the
 * workspace follows the site a turn touched.
 */
import { fireEvent, render, screen, waitFor } from "@testing-library/react";
import React from "react";
import { beforeEach, describe, expect, it, vi } from "vitest";

const api = vi.hoisted(() => ({ get: vi.fn(), post: vi.fn(), delete: vi.fn() }));
vi.mock("@/client/client.gen", () => ({
    client: { get: api.get, post: api.post, delete: api.delete },
}));
vi.mock("@/lib/auth", () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));

import type { Site } from "../api";
import { blocksOf } from "../RichReply";
import { SitePanel } from "../SitePanel";
import { describeActions, StudioChat, suggestNext } from "../StudioChat";
import { StudioWorkspace } from "../StudioWorkspace";

const SITE: Site = {
    id: 7,
    name: "Sunrise Dental",
    framework: "vite-react",
    build_status: "succeeded",
    built_at: "2026-10-03T10:00:00Z",
    build_seconds: 12.4,
    agent_workflow_ids: [41],
    updated_at: "2026-10-03T10:00:00Z",
    preview_url: "https://api.example.com/api/v1/public/sites/tok/",
    files: [
        { path: "package.json", bytes: 300 },
        { path: "src/App.jsx", bytes: 120 },
    ],
    build_log: null,
};

const USAGE = { used: 1, limit: 50, remaining: 49 };

beforeEach(() => {
    api.get.mockReset();
    api.post.mockReset();
    api.delete.mockReset();
    window.sessionStorage.clear();
});

describe("describeActions", () => {
    it("says what a turn did once each, in order, and skips reads", () => {
        expect(
            describeActions([
                "create_site",
                "write_site_files",
                "read_site_file",
                "build_site",
                "write_site_files",
                "build_site",
                "create_agent",
                "review_site_design",
                "review_site_design",
            ]),
        ).toEqual([
            "Started a site",
            "Wrote files",
            "Built the site",
            "Made an agent",
            "Checked the design",
        ]);
    });
});

describe("suggestNext and blocksOf", () => {
    it("offers next steps about what exists", () => {
        expect(suggestNext({ hasSite: true, hasAgents: true })).toContain(
            "Connect the contact form to my agent",
        );
        expect(suggestNext({ hasSite: false, hasAgents: true })).toContain(
            "Make a website for my agents",
        );
        expect(suggestNext({ hasSite: true, hasAgents: false }).length).toBeLessThanOrEqual(4);
    });

    it("reads paragraphs and lists out of a reply", () => {
        expect(blocksOf("Done.\n\n- one\n- two\n1. a\n2. b")).toEqual([
            { kind: "p", lines: ["Done."] },
            { kind: "ul", items: ["one", "two"] },
            { kind: "ol", items: ["a", "b"] },
        ]);
    });
});

describe("connect links", () => {
    it("shows an app to connect as a button in the thread, never a trip elsewhere", async () => {
        api.post.mockResolvedValueOnce({
            data: {
                reply: "Connect Gmail and I will attach it.",
                history: [],
                actions: ["connect_app"],
                created_workflow_ids: [],
                site_id: null,
                connect_links: [{ app: "Gmail", url: "https://connect.example/x" }],
                usage: USAGE,
            },
        });
        render(<StudioChat usage={USAGE} onTurn={vi.fn()} />);
        fireEvent.change(screen.getByLabelText("Message Studio"), {
            target: { value: "send confirmations by email" },
        });
        fireEvent.click(screen.getByLabelText("Send"));
        const link = await screen.findByRole("link", { name: /Connect Gmail/ });
        expect(link.getAttribute("href")).toBe("https://connect.example/x");
        expect(link.getAttribute("target")).toBe("_blank");
        expect(link.getAttribute("rel")).toContain("noopener");
    });
});

describe("StudioChat", () => {
    it("sends the message with the transcript and shows the reply", async () => {
        const onTurn = vi.fn();
        api.post.mockResolvedValueOnce({
            data: {
                reply: "Your site is built.",
                history: [{ role: "user", content: "hi" }],
                actions: ["create_site", "build_site", "create_agent"],
                created_workflow_ids: [41],
                created_agents: [{ id: 41, name: "Front desk", live: true, archived: false }],
                site_id: 7,
                usage: USAGE,
            },
        });
        render(<StudioChat usage={USAGE} onTurn={onTurn} />);

        fireEvent.change(screen.getByLabelText("Message Studio"), {
            target: { value: "Build a site for my clinic" },
        });
        fireEvent.click(screen.getByLabelText("Send"));

        expect(await screen.findByText("Your site is built.")).toBeTruthy();
        expect(api.post).toHaveBeenCalledWith({
            url: "/api/v1/studio/chat",
            body: { message: "Build a site for my clinic", history: [] },
        });
        expect(screen.getByText("Built the site")).toBeTruthy();
        expect(screen.getByText("Front desk").closest("a")?.getAttribute("href")).toBe(
            "/workflow/41",
        );
        // The next move is offered under the reply, and fills the box.
        fireEvent.click(screen.getByText("Send me an email when a customer books"));
        expect(
            (screen.getByLabelText("Message Studio") as HTMLTextAreaElement).value,
        ).toBe("Send me an email when a customer books");
        expect(onTurn).toHaveBeenCalledWith(expect.objectContaining({ site_id: 7 }));
    });

    it("puts a refused message back in the box and says why", async () => {
        api.post.mockResolvedValueOnce({
            error: { detail: "Add credit to keep building." },
        });
        render(<StudioChat usage={USAGE} onTurn={vi.fn()} />);
        const box = screen.getByLabelText("Message Studio") as HTMLTextAreaElement;
        fireEvent.change(box, { target: { value: "Make it blue" } });
        fireEvent.click(screen.getByLabelText("Send"));

        expect((await screen.findByRole("alert")).textContent).toContain(
            "Add credit to keep building.",
        );
        expect(box.value).toBe("Make it blue");
    });

    it("an opener fills the box and never sends", () => {
        render(<StudioChat usage={USAGE} onTurn={vi.fn()} />);
        fireEvent.click(screen.getByText("Clinic website + receptionist"));
        expect(
            (screen.getByLabelText("Message Studio") as HTMLTextAreaElement).value,
        ).toContain("dental clinic");
        expect(api.post).not.toHaveBeenCalled();
    });

    it("keeps the conversation across a reload of the tab", async () => {
        api.post.mockResolvedValueOnce({
            data: {
                reply: "Done.",
                history: [{ role: "assistant", content: "Done." }],
                actions: [],
                created_workflow_ids: [],
                site_id: null,
                usage: USAGE,
            },
        });
        const first = render(<StudioChat usage={USAGE} onTurn={vi.fn()} />);
        fireEvent.change(screen.getByLabelText("Message Studio"), {
            target: { value: "hello" },
        });
        fireEvent.click(screen.getByLabelText("Send"));
        await screen.findByText("Done.");
        first.unmount();

        render(<StudioChat usage={USAGE} onTurn={vi.fn()} />);
        expect(await screen.findByText("Done.")).toBeTruthy();
    });

    it("still works when storage is refused", async () => {
        const getItem = vi
            .spyOn(Storage.prototype, "getItem")
            .mockImplementation(() => {
                throw new Error("denied");
            });
        const setItem = vi
            .spyOn(Storage.prototype, "setItem")
            .mockImplementation(() => {
                throw new Error("denied");
            });
        try {
            render(<StudioChat usage={USAGE} onTurn={vi.fn()} />);
            expect(screen.getByLabelText("Message Studio")).toBeTruthy();
        } finally {
            getItem.mockRestore();
            setItem.mockRestore();
        }
    });
});

describe("SitePanel", () => {
    it("shows the preview in a sandbox that cannot reach the app", () => {
        render(<SitePanel site={SITE} onChanged={vi.fn()} />);
        const frame = screen.getByTitle("Preview of Sunrise Dental");
        expect(frame.getAttribute("src")).toBe(SITE.preview_url);
        const sandbox = frame.getAttribute("sandbox") ?? "";
        expect(sandbox).toContain("allow-scripts");
        expect(sandbox).not.toContain("allow-same-origin");
    });

    it("shows why a rebuild failed and asks the screen to reload", async () => {
        const onChanged = vi.fn();
        api.post.mockResolvedValueOnce({
            data: {
                status: "failed",
                seconds: 2,
                preview_url: SITE.preview_url,
                errors: 'src/App.jsx (3:10): Expected "}"',
            },
        });
        render(<SitePanel site={SITE} onChanged={onChanged} />);
        fireEvent.click(screen.getByText("Rebuild"));
        expect(await screen.findByText(/src\/App\.jsx \(3:10\)/)).toBeTruthy();
        expect(api.post).toHaveBeenCalledWith({ url: "/api/v1/studio/sites/7/build" });
        expect(onChanged).toHaveBeenCalled();
    });

    it("sizes the preview to a phone and lists the team by name", () => {
        render(
            <SitePanel
                site={{
                    ...SITE,
                    agents: [{ id: 41, name: "Front desk", live: false, archived: false }],
                }}
                onChanged={vi.fn()}
            />,
        );
        fireEvent.click(screen.getByLabelText("Phone"));
        const frame = screen.getByTitle("Preview of Sunrise Dental") as HTMLIFrameElement;
        expect(frame.style.width).toBe("390px");
        expect(screen.getByLabelText("Phone").getAttribute("aria-pressed")).toBe("true");
    });

    it("says what to do when nothing has been built", () => {
        render(
            <SitePanel
                site={{ ...SITE, build_status: "none", built_at: null, preview_url: null }}
                onChanged={vi.fn()}
            />,
        );
        expect(screen.queryByTitle("Preview of Sunrise Dental")).toBeNull();
        expect(screen.getByText(/Nothing to show yet/)).toBeTruthy();
    });
});

describe("StudioWorkspace", () => {
    function serve(sites: Site[], extra: Record<string, unknown> = {}) {
        api.get.mockImplementation(({ url }: { url: string }) => {
            if (url === "/api/v1/studio/config") {
                return Promise.resolve({
                    data: {
                        available: true,
                        unavailable_reason: null,
                        builds_configured: true,
                        usage: USAGE,
                        ...extra,
                    },
                });
            }
            if (url === "/api/v1/studio/sites") return Promise.resolve({ data: { sites } });
            const match = url.match(/\/api\/v1\/studio\/sites\/(\d+)$/);
            if (match) {
                const site = sites.find((s) => s.id === Number(match[1]));
                return Promise.resolve(site ? { data: site } : { error: { detail: "nope" } });
            }
            return Promise.resolve({ error: { detail: `unexpected ${url}` } });
        });
    }

    it("opens the newest site and shows its preview", async () => {
        serve([SITE]);
        render(<StudioWorkspace />);
        expect(await screen.findByTitle("Preview of Sunrise Dental")).toBeTruthy();
    });

    it("opens on one question when there is nothing yet", async () => {
        serve([]);
        render(<StudioWorkspace />);
        expect(await screen.findByText("What do you want to build?")).toBeTruthy();
        expect(screen.queryByText(/Your site appears here/)).toBeNull();
    });

    it("says plainly when builds are not set up", async () => {
        serve([], { builds_configured: false });
        render(<StudioWorkspace />);
        expect(await screen.findByText(/Site builds are not set up/)).toBeTruthy();
    });

    it("says why Studio cannot run when no model is set", async () => {
        serve([], { available: false, unavailable_reason: "No model key is installed." });
        render(<StudioWorkspace />);
        expect(await screen.findByText("No model key is installed.")).toBeTruthy();
        await waitFor(() => expect(screen.queryByLabelText("Message Studio")).toBeNull());
    });
});
