/**
 * Whether an agent is one prompt or a flow, and how to read and write it as one
 * prompt when it is.
 *
 * Both products we are measured against decide this by *agent type*, not by a
 * view toggle. Vapi has an Assistant — a first message and a system prompt on
 * one screen — and, separately, a Squad with a canvas. Bolna has a
 * `simple_llm_agent` and, separately, a graph agent, and says plainly that a
 * graph is for "discrete stages with different objectives" while "for a
 * single-objective agent that just answers questions, a regular
 * `simple_llm_agent` is enough".
 *
 * Decibyl has only ever had the graph. An agent that answers one question is
 * still four nodes on a canvas, which is why creating one takes a wizard and
 * editing one takes a tutorial.
 *
 * The type is **derived from the graph, not stored beside it**. A stored flag
 * can disagree with the thing it describes — an agent marked simple that has
 * grown a branch is a screen that cannot show what the agent does, and the
 * disagreement would only surface on a call. Deriving it costs one pass over
 * the nodes and cannot go stale. Add a second agent, a branch or a wait and the
 * agent stops being simple, in the editor and in fact at the same moment.
 */

import type { FlowEdge, FlowNode, FlowNodeData } from "./types";

/** Node types a one-prompt agent is allowed to contain. */
const SIMPLE_TYPES = new Set(["globalNode", "startCall", "agentNode", "endCall", "qa"]);

export type SimpleAgentFields = {
    /** What the agent says first. Empty means it waits for the caller. */
    firstMessage: string;
    /** The instruction the agent runs on — the system prompt. */
    systemPrompt: string;
    /** Shared persona, applied on top of the prompt. Decibyl keeps this in its
     *  own node rather than folding it into the prompt, so it stays separate
     *  here too rather than being silently concatenated. */
    persona: string;
    /** Tools the agent may call, by uuid. */
    toolUuids: string[];
    /** Knowledge-base documents the agent may cite, by uuid. */
    documentUuids: string[];
};

/**
 * True when this graph is one agent doing one job.
 *
 * Deliberately strict. Anything that routes — a branch, a wait, a webhook, a
 * second agent — means the canvas is showing something a single prompt cannot,
 * and a form that hid it would be lying about the agent.
 */
export function isSimpleAgent(nodes: FlowNode[], edges: FlowEdge[]): boolean {
    if (nodes.length === 0) return false;

    for (const node of nodes) {
        if (!SIMPLE_TYPES.has(node.type)) return false;
    }

    if (nodes.filter((n) => n.type === "agentNode").length !== 1) return false;
    if (nodes.filter((n) => n.type === "startCall").length > 1) return false;
    if (nodes.filter((n) => n.type === "globalNode").length > 1) return false;

    // A fork means a decision the prompt does not describe. One way out of each
    // node is what makes the flow expressible as a single instruction.
    const outgoing = new Map<string, number>();
    for (const edge of edges) {
        outgoing.set(edge.source, (outgoing.get(edge.source) ?? 0) + 1);
    }
    return [...outgoing.values()].every((count) => count <= 1);
}

const find = (nodes: FlowNode[], type: string) => nodes.find((n) => n.type === type);

/** The one-prompt view of a graph that `isSimpleAgent` accepts. */
export function readSimpleAgent(nodes: FlowNode[]): SimpleAgentFields {
    const start = find(nodes, "startCall");
    const agent = find(nodes, "agentNode");
    const global = find(nodes, "globalNode");

    return {
        firstMessage: (start?.data.greeting as string | undefined) ?? "",
        systemPrompt: (agent?.data.prompt as string | undefined) ?? "",
        persona: (global?.data.prompt as string | undefined) ?? "",
        toolUuids: (agent?.data.tool_uuids as string[] | undefined) ?? [],
        documentUuids: (agent?.data.document_uuids as string[] | undefined) ?? [],
    };
}

/**
 * The same nodes with one field changed.
 *
 * Returns a new array and new data objects for the nodes it touches, leaving
 * every other node identity-equal — React Flow and the dirty check both compare
 * by reference, so copying the whole graph on each keystroke would re-render
 * the canvas and mark clean edits dirty.
 *
 * A field with nowhere to go is dropped rather than invented: an agent with no
 * start node has no first message to set, and creating one here would add a
 * node the canvas never showed the author.
 */
export function writeSimpleAgent(
    nodes: FlowNode[],
    patch: Partial<SimpleAgentFields>,
): FlowNode[] {
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
        if (node.type === "agentNode") {
            const data: Partial<FlowNodeData> = {};
            if (patch.systemPrompt !== undefined) data.prompt = patch.systemPrompt;
            if (patch.toolUuids !== undefined) data.tool_uuids = patch.toolUuids;
            if (patch.documentUuids !== undefined) data.document_uuids = patch.documentUuids;
            if (Object.keys(data).length > 0) return set(node, data);
        }
        return node;
    });
}
