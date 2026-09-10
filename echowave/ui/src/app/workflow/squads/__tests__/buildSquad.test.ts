import { describe, expect, it } from "vitest";

import { buildSquadDefinition } from "../buildSquad";

describe("a squad from the form", () => {
    it("is a front desk with one handoff per member, each on its own condition", () => {
        const def = buildSquadDefinition({
            greeting: "Hello.",
            prompt: "Route the call.",
            persona: "Be brief.",
            members: [
                { agentUuid: "u-billing", name: "Billing", when: "They ask about a bill" },
                { agentUuid: "u-booking", name: "Booking", when: "They want an appointment" },
            ],
        });
        const types = def.nodes.map((n) => n.type);
        expect(types).toEqual(["globalNode", "startCall", "handoff", "handoff"]);
        const start = def.nodes[1] as { data: Record<string, unknown> };
        expect(start.data).toMatchObject({ is_start: true, greeting: "Hello.", prompt: "Route the call." });
        expect(def.nodes[2]).toMatchObject({ data: { name: "Billing", agent_uuid: "u-billing" } });
        expect(def.edges).toHaveLength(2);
        expect(def.edges[1]).toMatchObject({
            source: "start-1",
            target: "handoff-2",
            data: { label: "Booking", condition: "They want an appointment" },
        });
    });
});
