/**
 * A multi-step agent, read and written as a form.
 *
 * `simpleAgent` covers the one-prompt case. Everything a template makes is
 * not that: a clinic agent is an answer step, a booking step, a callback step
 * and a close, and until now the only screen for it was the canvas — which
 * is what opened, by default, for every account that started from a
 * template. Vapi and Bolna open an agent on its prompts and keep the graph a
 * click away; this is the read/write layer for that screen.
 *
 * The form shows what a form can show honestly: the first words, the shared
 * rules, and each step's own instruction, in graph order. It does not show
 * the branching — which step follows which, and when — because a textarea
 * cannot, and that stays on the canvas, one button away.
 *
 * Like `simpleAgent`, this returns new objects only for the nodes it touches.
 */
import type { FlowEdge, FlowNode, FlowNodeData } from "./types";

/** Node types that carry a prompt a person would edit on this screen. */
const STEP_TYPES = new Set(["startCall", "agentNode", "endCall", "handoff"]);

export type FlowStep = {
    id: string;
    type: string;
    name: string;
    prompt: string;
    /** Set on a handoff: the agent that takes the call from here. */
    agentUuid?: string;
    toolCount: number;
    documentCount: number;
};

export type FlowAgentFields = {
    firstMessage: string;
    persona: string;
    steps: FlowStep[];
};

/**
 * Steps in the order a call meets them: a walk from the start node along the
 * edges, then anything the walk did not reach (detached or unreachable
 * nodes), so nothing on the canvas is missing from the form.
 */
export function orderSteps(nodes: FlowNode[], edges: FlowEdge[]): FlowNode[] {
    const byId = new Map(nodes.map((n) => [n.id, n]));
    const outgoing = new Map<string, string[]>();
    for (const edge of edges) {
        const list = outgoing.get(edge.source) ?? [];
        list.push(edge.target);
        outgoing.set(edge.source, list);
    }
    const seen = new Set<string>();
    const ordered: FlowNode[] = [];
    const visit = (id: string) => {
        if (seen.has(id)) return;
        const node = byId.get(id);
        if (!node) return;
        seen.add(id);
        if (STEP_TYPES.has(node.type)) ordered.push(node);
        for (const next of outgoing.get(id) ?? []) visit(next);
    };
    const start = nodes.find((n) => n.type === "startCall");
    if (start) visit(start.id);
    for (const node of nodes) {
        if (!seen.has(node.id) && STEP_TYPES.has(node.type)) {
            seen.add(node.id);
            ordered.push(node);
        }
    }
    return ordered;
}

export function readFlowAgent(nodes: FlowNode[], edges: FlowEdge[]): FlowAgentFields {
    const start = nodes.find((n) => n.type === "startCall");
    const global = nodes.find((n) => n.type === "globalNode");
    return {
        firstMessage: (start?.data.greeting as string | undefined) ?? "",
        persona: (global?.data.prompt as string | undefined) ?? "",
        steps: orderSteps(nodes, edges).map((node) => ({
            id: node.id,
            type: node.type,
            name: node.data.name ?? "",
            prompt: (node.data.prompt as string | undefined) ?? "",
            agentUuid: (node.data as { agent_uuid?: string }).agent_uuid,
            toolCount: ((node.data.tool_uuids as string[] | undefined) ?? []).length,
            documentCount: ((node.data.document_uuids as string[] | undefined) ?? []).length,
        })),
    };
}

export type FlowAgentPatch = {
    firstMessage?: string;
    persona?: string;
    /** One step's prompt, by node id. */
    step?: { id: string; prompt: string };
};

export function writeFlowAgent(nodes: FlowNode[], patch: FlowAgentPatch): FlowNode[] {
    const set = (node: FlowNode, data: Partial<FlowNodeData>): FlowNode => ({
        ...node,
        data: { ...node.data, ...data },
    });
    return nodes.map((node) => {
        if (node.type === "startCall" && patch.firstMessage !== undefined) {
            return set(node, { greeting: patch.firstMessage, greeting_type: "text" });
        }
        if (node.type === "globalNode" && patch.persona !== undefined) {
            return set(node, { prompt: patch.persona });
        }
        if (patch.step && node.id === patch.step.id) {
            return set(node, { prompt: patch.step.prompt });
        }
        return node;
    });
}
