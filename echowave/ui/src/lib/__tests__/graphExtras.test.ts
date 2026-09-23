/**
 * G-1's decoration: a "Starts when" card joined to the start step, each
 * step marked by whether the last run reached it, each step's apps -- and
 * nothing at all when the feature is off. None of it is ever saved.
 */
import { describe, expect, it } from "vitest";

import type { ToolResponse } from "@/client/types.gen";

import { appsOf, decorate, isGraphExtra, STARTS_EDGE_ID, STARTS_NODE_ID, type StepApp } from "../graphExtras";

const node = (id: string, type: string, name: string, extra: Record<string, unknown> = {}) => ({
    id,
    type,
    position: { x: 400, y: 100 },
    data: { name, ...extra },
});
const NODES = [
    node("1", "startCall", "Answer", { tool_uuids: ["t-cal", "t-sheet", "t-http"] }),
    node("2", "agentNode", "Book"),
    node("3", "endCall", "Close"),
    node("9", "trigger", "Webhook"),
];
const EDGES = [{ id: "e1", source: "1", target: "2" }];
const tool = (uuid: string, category: string, toolkit?: string) =>
    ({ tool_uuid: uuid, name: uuid, category, definition: toolkit ? { config: { toolkit } } : {} }) as unknown as ToolResponse;
const TOOLS = [
    tool("t-cal", "google_calendar"),
    tool("t-sheet", "composio", "GOOGLESHEETS"),
    tool("t-http", "http_api"),
];
const LOGOS = new Map<string, StepApp>([
    ["googlesheets", { slug: "googlesheets", name: "Google Sheets", logo: "https://logo/sheets.png" }],
]);

describe("decorating the graph", () => {
    it("changes nothing while the feature is off", () => {
        const out = decorate(NODES, EDGES, null, TOOLS, LOGOS);
        expect(out.nodes).toBe(NODES);
        expect(out.edges).toBe(EDGES);
    });

    it("puts a Starts-when card before the start step, joined to it", () => {
        const starts = [{ kind: "routine", label: "Morning report", detail: "every weekday", active: true }];
        const out = decorate(NODES, EDGES, { starts, lastRun: null }, TOOLS, LOGOS);
        const card = out.nodes[0];
        expect(card.id).toBe(STARTS_NODE_ID);
        expect(card.type).toBe("startsCard");
        expect(card.position.x).toBeLessThan(400);
        expect(card.data).toEqual({ starts });
        expect(card.draggable).toBe(false);
        expect(card.deletable).toBe(false);
        expect(out.edges.at(-1)).toMatchObject({ id: STARTS_EDGE_ID, source: STARTS_NODE_ID, target: "1" });
        expect(isGraphExtra(card.id) && isGraphExtra(STARTS_EDGE_ID)).toBe(true);
        expect(out.nodes.slice(1).map((n) => n.id)).toEqual(["1", "2", "3", "9"]);
    });

    it("marks each step by whether the last run reached it, by id or by name", () => {
        const lastRun = { id: 5, at: "2026-09-23", visited_ids: ["1"], visited_names: ["Book"] };
        const out = decorate(NODES, EDGES, { starts: [], lastRun }, TOOLS, LOGOS);
        const mark = (id: string) => (out.nodes.find((n) => n.id === id)?.data as { last_run?: string }).last_run;
        expect(mark("1")).toBe("reached");
        expect(mark("2")).toBe("reached");
        expect(mark("3")).toBe("missed");
        // A trigger is not a step a run passes through.
        expect(mark("9")).toBeUndefined();
    });

    it("marks nothing when the agent has never run", () => {
        const out = decorate(NODES, EDGES, { starts: [], lastRun: null }, TOOLS, LOGOS);
        expect(out.nodes.some((n) => (n.data as { last_run?: string }).last_run)).toBe(false);
    });

    it("never changes the nodes it was given", () => {
        const before = JSON.stringify(NODES);
        decorate(NODES, EDGES, { starts: [], lastRun: { id: 1, at: "", visited_ids: ["1"], visited_names: [] } }, TOOLS, LOGOS);
        expect(JSON.stringify(NODES)).toBe(before);
    });
});

describe("a step's apps", () => {
    it("names the apps its tools act on, with a logo where one is known", () => {
        expect(appsOf(["t-cal", "t-sheet", "t-http"], TOOLS, LOGOS)).toEqual([
            { slug: "googlecalendar", name: "googlecalendar", logo: null },
            { slug: "googlesheets", name: "Google Sheets", logo: "https://logo/sheets.png" },
        ]);
    });

    it("shows at most three, each once", () => {
        const many = ["a", "b", "c", "d", "e"].map((s) => tool(`t-${s}`, "composio", s));
        expect(appsOf([...many.map((t) => t.tool_uuid), "t-a"], many, new Map())).toHaveLength(3);
    });
});
