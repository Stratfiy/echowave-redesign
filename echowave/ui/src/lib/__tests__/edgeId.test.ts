import { describe, expect, it } from "vitest";

import { nextEdgeId } from "@/lib/utils";

describe("nextEdgeId", () => {
    it("keeps the readable form for the first connection between two steps", () => {
        expect(nextEdgeId("start", "ask", [])).toBe("start-ask");
    });

    it("gives a second path between the same steps its own ID", () => {
        const edges = [{ id: "start-ask" }];
        expect(nextEdgeId("start", "ask", edges)).toBe("start-ask-2");
    });

    it("keeps numbering past every suffix already in use", () => {
        const edges = [{ id: "start-ask" }, { id: "start-ask-2" }, { id: "start-ask-3" }];
        expect(nextEdgeId("start", "ask", edges)).toBe("start-ask-4");
    });

    it("fills a gap left by a deleted connection rather than skipping it", () => {
        const edges = [{ id: "start-ask" }, { id: "start-ask-3" }];
        expect(nextEdgeId("start", "ask", edges)).toBe("start-ask-2");
    });

    it("never collides with an unrelated edge that already owns the name", () => {
        // A saved graph may carry any ID at all, including one that looks like
        // the form this allocates.
        const edges = [{ id: "start-ask" }, { id: "start-ask-2" }];
        const allocated = nextEdgeId("start", "ask", edges);
        expect(edges.some((edge) => edge.id === allocated)).toBe(false);
    });

    it("does not let the reverse direction take the forward name", () => {
        const edges = [{ id: "ask-start" }];
        expect(nextEdgeId("start", "ask", edges)).toBe("start-ask");
    });
});
