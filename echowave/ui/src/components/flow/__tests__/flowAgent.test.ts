import { describe, expect, it } from "vitest";

import { orderSteps, readFlowAgent, writeFlowAgent } from "../flowAgent";
import type { FlowEdge, FlowNode } from "../types";

const node = (id: string, type: string, data: Record<string, unknown>): FlowNode =>
    ({ id, type, position: { x: 0, y: 0 }, data: { name: id, ...data } }) as unknown as FlowNode;
const edge = (source: string, target: string): FlowEdge =>
    ({ id: `${source}-${target}`, source, target, data: { condition: "", label: "" } }) as FlowEdge;

const nodes = [
    node("global", "globalNode", { prompt: "Be warm." }),
    node("qa", "qa", {}),
    node("close", "endCall", { prompt: "Say goodbye." }),
    node("book", "agentNode", { prompt: "Book it.", tool_uuids: ["t1"] }),
    node("start", "startCall", { greeting: "Namaste.", prompt: "Find out what they need." }),
    node("billing", "handoff", { agent_uuid: "abc" }),
];
const edges = [edge("start", "book"), edge("book", "close"), edge("start", "billing")];

describe("a multi-step agent as a form", () => {
    it("lists steps in call order, not canvas order, and leaves out what has no prompt", () => {
        expect(orderSteps(nodes, edges).map((n) => n.id)).toEqual(["start", "book", "close", "billing"]);
    });

    it("still lists a step the walk cannot reach", () => {
        const orphan = node("lost", "agentNode", { prompt: "Unreachable." });
        expect(orderSteps([...nodes, orphan], edges).map((n) => n.id)).toContain("lost");
    });

    it("reads the first message, the rules and each step", () => {
        const fields = readFlowAgent(nodes, edges);
        expect(fields.firstMessage).toBe("Namaste.");
        expect(fields.persona).toBe("Be warm.");
        expect(fields.steps[1]).toMatchObject({ id: "book", prompt: "Book it.", toolCount: 1 });
        expect(fields.steps[3]).toMatchObject({ type: "handoff", agentUuid: "abc" });
    });

    it("writes one step's prompt and touches nothing else", () => {
        const next = writeFlowAgent(nodes, { step: { id: "book", prompt: "Book it carefully." } });
        expect(next.find((n) => n.id === "book")?.data.prompt).toBe("Book it carefully.");
        // Untouched nodes keep their identity, so the canvas does not re-render them.
        expect(next.find((n) => n.id === "start")).toBe(nodes.find((n) => n.id === "start"));
    });

    it("writes the first message as text", () => {
        const next = writeFlowAgent(nodes, { firstMessage: "Hello." });
        expect(next.find((n) => n.id === "start")?.data).toMatchObject({ greeting: "Hello.", greeting_type: "text" });
    });
});
