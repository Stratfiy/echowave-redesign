import { describe, expect, it } from "vitest";

import { latestTurnStatus, sourcesForReply } from "../taskState";

let id = 0;
const row = (kind: string, actor: string, payload: Record<string, unknown> = {}, summary = "") => ({
    id: ++id,
    at: new Date(2026, 9, 7, 10, 0, id).toISOString(),
    kind,
    actor,
    payload,
    summary,
    workflow_id: null,
});

describe("latestTurnStatus", () => {
    it("has nothing to say before the person has said anything", () => {
        expect(latestTurnStatus([], false)).toBeNull();
        expect(latestTurnStatus([row("message", "agent")], false)).toBeNull();
    });

    it("is running with the real stage while the reply is forming", () => {
        const rows = [row("message", "human"), row("activity", "agent", {}, "Read the team (2 agents)")];
        expect(latestTurnStatus(rows, true)).toMatchObject({ state: "running", stage: "Read the team (2 agents)" });
    });

    it("reads completed only from a reply the server wrote", () => {
        const ask = row("message", "human");
        const rows = [ask, row("message", "agent", { body: "Here." })];
        expect(latestTurnStatus(rows, false)).toMatchObject({ state: "completed", requestId: ask.id });
    });

    it("marks a stopped reply partial and a refused one failed", () => {
        expect(latestTurnStatus([row("message", "human"), row("message", "agent", { stopped: true })], false)?.state).toBe("partial");
        expect(latestTurnStatus([row("message", "human"), row("message", "agent", { failed: true })], false)?.state).toBe("failed");
    });

    it("waits on the person for a card they have not answered", () => {
        expect(latestTurnStatus([row("message", "human"), row("action_proposed", "agent", { state: "proposed" })], false)?.state).toBe(
            "awaiting_approval",
        );
        expect(latestTurnStatus([row("message", "human"), row("action_proposed", "agent", { state: "done" }), row("message", "agent")], false)?.state).toBe(
            "completed",
        );
        expect(latestTurnStatus([row("message", "human"), row("needs_secret", "agent", {})], false)?.state).toBe("needs_input");
        expect(latestTurnStatus([row("message", "human"), row("needs_decision", "agent", { decided: { choice: ["a"] } })], true)?.state).toBe(
            "running",
        );
    });

    it("waits on the person for a learning card until they press", () => {
        expect(latestTurnStatus([row("message", "human"), row("skill_lesson", "agent", { type: "remembered" })], false)?.state).toBe(
            "awaiting_approval",
        );
        expect(
            latestTurnStatus(
                [row("message", "human"), row("skill_lesson", "agent", { decided: { action: "publish" } }), row("message", "agent")],
                false,
            )?.state,
        ).toBe("completed");
    });

    it("describes only the latest turn", () => {
        const rows = [row("message", "human"), row("message", "agent", { failed: true }), row("message", "human")];
        expect(latestTurnStatus(rows, true)?.state).toBe("running");
    });
});

describe("sourcesForReply", () => {
    it("reads the sources from the readings row of the same turn", () => {
        const sources = [{ kind: "team", label: "Your team", status: "read" }];
        const reading = row("activity", "agent", { sources });
        const reply = row("message", "agent");
        expect(sourcesForReply([row("message", "human"), reading, reply], reply.id)).toEqual(sources);
    });

    it("shows every file the turn read, from the readings and a later search", () => {
        const readings = row("activity", "agent", {
            sources: [
                { kind: "team", label: "Your team", status: "read" },
                { kind: "knowledge", label: "Company knowledge", status: "read", documents: ["rates.xlsx, sheet Rates, row 4"] },
            ],
        });
        const search = row("activity", "agent", {
            sources: [{ kind: "knowledge", label: "Files", status: "read", documents: ["board.jpg (in Menus)"] }],
        });
        const again = row("activity", "agent", {
            sources: [{ kind: "knowledge", label: "Files", status: "read", documents: ["board.jpg (in Menus)", "hours.txt"] }],
        });
        const reply = row("message", "agent");
        const sources = sourcesForReply([row("message", "human"), readings, search, again, reply], reply.id);
        expect(sources.map((s) => s.label)).toEqual(["Your team", "Company knowledge", "Files"]);
        expect(sources[2].documents).toEqual(["board.jpg (in Menus)", "hours.txt"]);
    });

    it("never borrows an earlier turn's sources", () => {
        const earlier = row("activity", "agent", { sources: [{ kind: "team", label: "x", status: "read" }] });
        const reply = row("message", "agent");
        expect(sourcesForReply([earlier, row("message", "human"), reply], reply.id)).toEqual([]);
    });
});
