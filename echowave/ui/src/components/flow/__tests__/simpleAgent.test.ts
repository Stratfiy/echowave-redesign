/**
 * The rule that decides whether an agent gets a form or a canvas.
 *
 * Getting this wrong is worse than having no form at all: a graph misread as
 * simple renders a screen that cannot show what the agent does, and the author
 * edits a prompt believing that is the whole agent while a branch they cannot
 * see routes the call somewhere else.
 */

import { describe, expect, it } from "vitest";

import {
    isSimpleAgent,
    readSimpleAgent,
    writeSimpleAgent,
} from "../simpleAgent";
import type { FlowEdge, FlowNode } from "../types";

const node = (id: string, type: string, data: Record<string, unknown> = {}): FlowNode => ({
    id,
    type,
    position: { x: 0, y: 0 },
    data: { name: id, ...data },
});

const edge = (source: string, target: string): FlowEdge => ({
    id: `${source}-${target}`,
    source,
    target,
    data: { label: "continue", condition: "" },
});

/** The shape the wizard and every template produce. */
const simple = () => [
    node("global-1", "globalNode", { prompt: "Be warm and brief." }),
    node("start-1", "startCall", { greeting: "Hello, Sunrise Clinic." }),
    node("agent-1", "agentNode", { prompt: "Book the appointment.", tool_uuids: ["t1"] }),
    node("end-1", "endCall", { prompt: "Confirm and say goodbye." }),
];
const simpleEdges = () => [edge("start-1", "agent-1"), edge("agent-1", "end-1")];

describe("isSimpleAgent", () => {
    it("accepts the shape the wizard and templates produce", () => {
        expect(isSimpleAgent(simple(), simpleEdges())).toBe(true);
    });

    it("accepts a QA node, which changes nothing a caller hears", () => {
        const nodes = [...simple(), node("qa-1", "qa", { qa_enabled: true })];
        expect(isSimpleAgent(nodes, simpleEdges())).toBe(true);
    });

    it("rejects two agents — that is a squad, and a form cannot show a handoff", () => {
        const nodes = [...simple(), node("agent-2", "agentNode", { prompt: "Escalate." })];
        expect(isSimpleAgent(nodes, simpleEdges())).toBe(false);
    });

    it("rejects a branch, because the decision it makes is not in any prompt", () => {
        const nodes = [...simple(), node("branch-1", "branch")];
        expect(isSimpleAgent(nodes, simpleEdges())).toBe(false);
    });

    it("rejects a wait node", () => {
        const nodes = [...simple(), node("wait-1", "wait")];
        expect(isSimpleAgent(nodes, simpleEdges())).toBe(false);
    });

    it("rejects a webhook, which does something the prompt does not describe", () => {
        const nodes = [...simple(), node("hook-1", "webhook")];
        expect(isSimpleAgent(nodes, simpleEdges())).toBe(false);
    });

    it("rejects a fork even when every node type is allowed", () => {
        const nodes = [...simple(), node("end-2", "endCall", { prompt: "Other ending." })];
        const edges = [...simpleEdges(), edge("agent-1", "end-2")];
        expect(isSimpleAgent(nodes, edges)).toBe(false);
    });

    it("rejects an empty graph rather than offering a form for nothing", () => {
        expect(isSimpleAgent([], [])).toBe(false);
    });

    it("rejects a graph with no agent at all", () => {
        const nodes = [node("start-1", "startCall"), node("end-1", "endCall")];
        expect(isSimpleAgent(nodes, [edge("start-1", "end-1")])).toBe(false);
    });
});

describe("readSimpleAgent", () => {
    it("reads the first message, the prompt and the persona from their nodes", () => {
        const fields = readSimpleAgent(simple());
        expect(fields.firstMessage).toBe("Hello, Sunrise Clinic.");
        expect(fields.systemPrompt).toBe("Book the appointment.");
        expect(fields.persona).toBe("Be warm and brief.");
        expect(fields.toolUuids).toEqual(["t1"]);
    });

    it("returns empty strings rather than undefined for missing nodes", () => {
        const fields = readSimpleAgent([node("agent-1", "agentNode")]);
        expect(fields.firstMessage).toBe("");
        expect(fields.systemPrompt).toBe("");
        expect(fields.persona).toBe("");
        expect(fields.toolUuids).toEqual([]);
    });
});

describe("writeSimpleAgent", () => {
    it("writes each field to the node that owns it", () => {
        const next = writeSimpleAgent(simple(), {
            firstMessage: "Good morning.",
            systemPrompt: "Qualify the caller.",
            persona: "Be direct.",
        });
        const fields = readSimpleAgent(next);
        expect(fields.firstMessage).toBe("Good morning.");
        expect(fields.systemPrompt).toBe("Qualify the caller.");
        expect(fields.persona).toBe("Be direct.");
    });

    it("leaves untouched nodes identity-equal", () => {
        const before = simple();
        const after = writeSimpleAgent(before, { systemPrompt: "New." });
        const index = (nodes: FlowNode[], id: string) => nodes.find((n) => n.id === id);
        // The canvas and the dirty check both compare by reference: copying
        // every node on each keystroke would re-render the flow and mark a
        // clean edit dirty.
        expect(index(after, "end-1")).toBe(index(before, "end-1"));
        expect(index(after, "start-1")).toBe(index(before, "start-1"));
        expect(index(after, "agent-1")).not.toBe(index(before, "agent-1"));
    });

    it("does not mutate the nodes it was given", () => {
        const before = simple();
        writeSimpleAgent(before, { systemPrompt: "New." });
        expect(readSimpleAgent(before).systemPrompt).toBe("Book the appointment.");
    });

    it("drops a field that has nowhere to go rather than inventing a node", () => {
        const noStart = [node("agent-1", "agentNode", { prompt: "Hi." })];
        const after = writeSimpleAgent(noStart, { firstMessage: "Hello." });
        expect(after).toHaveLength(1);
        expect(readSimpleAgent(after).firstMessage).toBe("");
    });

    it("sets greeting_type alongside the greeting, so a text greeting plays", () => {
        const after = writeSimpleAgent(simple(), { firstMessage: "Hi." });
        const start = after.find((n) => n.type === "startCall");
        expect(start?.data.greeting_type).toBe("text");
    });

    it("changes nothing when the patch is empty", () => {
        const before = simple();
        const after = writeSimpleAgent(before, {});
        after.forEach((n, i) => expect(n).toBe(before[i]));
    });
});
