"use client";

/**
 * What the agent graph shows beyond its steps (G-1): what starts the agent,
 * which steps its last run reached, and the apps each step acts on.
 *
 * All of it is decoration: `decorate` returns new display nodes and edges
 * and never touches the store, so nothing here is ever saved. The routes
 * are a 404 while AGENT_GRAPH_EXTRAS_ENABLED is off, and then the graph is
 * exactly what it was.
 */

import type { Edge, Node } from "@xyflow/react";
import { useEffect, useState } from "react";

import {
    agentLastRunApiV1AgentGraphWorkflowIdLastRunGet,
    agentStartsApiV1AgentGraphWorkflowIdStartsGet,
    listConnectorsApiV1ConnectorsGet,
} from "@/client/sdk.gen";
import type { ConnectorResponse, ToolResponse } from "@/client/types.gen";
import { useAuth } from "@/lib/auth";

export const STARTS_NODE_ID = "__graph_starts";
export const STARTS_EDGE_ID = "__graph_starts_edge";
/** Display-only ids: filtered out of every change before it reaches the store. */
export const isGraphExtra = (id: string) => id.startsWith("__graph_");

export interface Start {
    kind: "phone" | "routine" | "trigger" | "web" | "campaign" | string;
    label: string;
    detail: string | null;
    active: boolean;
}

export interface LastRun {
    id: number;
    at: string;
    visited_ids: string[];
    visited_names: string[];
}

export interface GraphExtras {
    starts: Start[];
    lastRun: LastRun | null;
}

export interface StepApp {
    slug: string;
    name: string;
    logo: string | null;
}

/** The steps a run passes through; triggers, reviews and personas are not. */
const STEP_TYPES = new Set(["startCall", "agentNode", "endCall"]);

export function useGraphExtras(workflowId: number): GraphExtras | null {
    const { user, loading } = useAuth();
    const [extras, setExtras] = useState<GraphExtras | null>(null);
    useEffect(() => {
        if (loading || !user) return;
        let live = true;
        void (async () => {
            const starts = await agentStartsApiV1AgentGraphWorkflowIdStartsGet({ path: { workflow_id: workflowId } });
            if (!live || starts.error) return;
            const run = await agentLastRunApiV1AgentGraphWorkflowIdLastRunGet({ path: { workflow_id: workflowId } });
            if (!live) return;
            setExtras({
                starts: ((starts.data as { starts?: Start[] })?.starts) ?? [],
                lastRun: run.error ? null : ((run.data as { run?: LastRun | null })?.run ?? null),
            });
        })();
        return () => {
            live = false;
        };
    }, [loading, user, workflowId]);
    return extras;
}

let logos: Promise<Map<string, StepApp>> | null = null;

/** App names and logos by slug, fetched once per page load. */
export function useAppLogos(enabled: boolean): Map<string, StepApp> {
    const [map, setMap] = useState<Map<string, StepApp>>(new Map());
    useEffect(() => {
        if (!enabled) return;
        let live = true;
        logos ??= listConnectorsApiV1ConnectorsGet()
            .then((result) => {
                const out = new Map<string, StepApp>();
                const data = result.data;
                const rows: ConnectorResponse[] = [
                    ...(data?.popular ?? []),
                    ...(data?.groups ?? []).flatMap((g) => g.connectors ?? []),
                    ...(data?.other ?? []),
                ];
                for (const c of rows) out.set(c.slug, { slug: c.slug, name: c.name, logo: c.logo ?? null });
                return out;
            })
            .catch(() => new Map());
        void logos.then((value) => {
            if (live) setMap(value);
        });
        return () => {
            live = false;
        };
    }, [enabled]);
    return map;
}

function appSlugOf(tool: ToolResponse): string | null {
    const config = (tool.definition as { config?: { toolkit?: string } } | null)?.config;
    if (tool.category === "composio" && config?.toolkit) return config.toolkit.toLowerCase();
    if (tool.category === "google_calendar") return "googlecalendar";
    return null;
}

/** Up to three apps a step's tools act on, named even without a logo. */
export function appsOf(
    toolUuids: string[] | undefined,
    tools: ToolResponse[],
    known: Map<string, StepApp>,
): StepApp[] {
    const byId = new Map(tools.map((t) => [t.tool_uuid, t]));
    const slugs: string[] = [];
    for (const uuid of toolUuids ?? []) {
        const tool = byId.get(uuid);
        const slug = tool ? appSlugOf(tool) : null;
        if (slug && !slugs.includes(slug)) slugs.push(slug);
    }
    return slugs.slice(0, 3).map((slug) => known.get(slug) ?? { slug, name: slug, logo: null });
}

export function decorate<N extends Node, E extends Edge>(
    nodes: N[],
    edges: E[],
    extras: GraphExtras | null,
    tools: ToolResponse[] | undefined,
    known: Map<string, StepApp>,
): { nodes: Node[]; edges: Edge[] } {
    if (!extras) return { nodes, edges };
    const run = extras.lastRun;
    const ids = new Set(run?.visited_ids ?? []);
    const names = new Set(run?.visited_names ?? []);
    const decorated: Node[] = nodes.map((node) => {
        const data = node.data as { name?: string; tool_uuids?: string[] };
        const apps = appsOf(data.tool_uuids, tools ?? [], known);
        const step = STEP_TYPES.has(node.type ?? "");
        const lastRun =
            run && step ? (ids.has(node.id) || (data.name && names.has(data.name)) ? "reached" : "missed") : undefined;
        if (!apps.length && !lastRun) return node;
        return {
            ...node,
            data: { ...node.data, ...(apps.length ? { apps } : {}), ...(lastRun ? { last_run: lastRun } : {}) },
        };
    });
    const start = nodes.find((node) => node.type === "startCall");
    if (!start) return { nodes: decorated, edges };
    const card: Node = {
        id: STARTS_NODE_ID,
        type: "startsCard",
        position: { x: start.position.x - 320, y: start.position.y },
        data: { starts: extras.starts },
        draggable: false,
        selectable: false,
        deletable: false,
        connectable: false,
        focusable: false,
    };
    const edge: Edge = {
        id: STARTS_EDGE_ID,
        source: STARTS_NODE_ID,
        target: start.id,
        type: "default",
        animated: true,
        deletable: false,
        selectable: false,
        focusable: false,
        style: { strokeDasharray: "4 4" },
    };
    return { nodes: [card, ...decorated], edges: [...edges, edge] };
}
