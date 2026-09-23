/**
 * How a bot is standing, in words rather than a status code.
 */

import { describe, expect, it } from "vitest";

import type { ReadinessItem, TeamMember } from "@/client/types.gen";

import { firstFault, standing } from "../AgentStanding";

function member(patch: Partial<TeamMember>): TeamMember {
    return {
        workflow_id: 1,
        workflow_uuid: null,
        name: "Front desk",
        is_live: true,
        status: "",
        tone: "good",
        at: null,
        calls: 0,
        answered: 0,
        outcomes: 0,
        failures: 0,
        last_action: null,
        ...patch,
    };
}

function item(patch: Partial<ReadinessItem>): ReadinessItem {
    return { app: "gmail", label: "Gmail", status: "ok", needed_by: [], recent_failures: 0, connectable: true, ...patch };
}

describe("an agent's standing", () => {
    it("says paused before it says anything else", () => {
        // A paused bot is not failing and not idle -- it was switched off,
        // and that is the answer to "why is nothing happening".
        expect(standing(member({ is_live: false, tone: "failing", calls: 9 })).label).toBe("Paused");
    });

    it("says failing over working", () => {
        expect(standing(member({ tone: "failing", calls: 4 })).label).toBe("Failing");
    });

    it("separates a quiet day from an agent that has never run", () => {
        expect(standing(member({ calls: 0 })).label).toBe("On, nothing today");
        expect(standing(null).label).toBe("Not started yet");
    });

    it("counts an agent that took calls as working", () => {
        expect(standing(member({ calls: 3, answered: 3 })).tone).toBe("good");
    });
});

describe("what is not working", () => {
    it("finds the first thing that is not ok", () => {
        const fault = firstFault([item({}), item({ app: "cal", label: "Calendar", status: "error" })]);
        expect(fault?.label).toBe("Calendar");
    });

    it("treats ready and ok as the same kind of fine", () => {
        expect(firstFault([item({ status: "ready" }), item({ status: "ok" })])).toBeNull();
    });

    it("says nothing when there is nothing to say", () => {
        expect(firstFault([])).toBeNull();
    });
});
