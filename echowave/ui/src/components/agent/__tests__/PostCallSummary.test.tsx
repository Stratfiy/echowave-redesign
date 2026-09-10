import { describe, expect, it } from "vitest";

import { verdictFromAnnotations } from "../PostCallSummary";

describe("verdictFromAnnotations", () => {
  it("reads a per-node QA result", () => {
    const verdict = verdictFromAnnotations({
      qa_1: {
        node_results: {
          greeting: { summary: "Caller asked for a refund; agent explained the policy.", score: 8, tags: ["refund"], overall_sentiment: "neutral" },
        },
      },
      tags: ["refund"],
    });
    expect(verdict).toEqual({
      summary: "Caller asked for a refund; agent explained the policy.",
      score: 8,
      sentiment: "neutral",
      tags: ["refund"],
    });
  });

  it("prefers the node that has a summary and tolerates tag objects", () => {
    const verdict = verdictFromAnnotations({
      qa: {
        node_results: {
          a: { summary: "", score: null, tags: [] },
          b: { summary: "Booked.", score: 9, tags: [{ tag: "booked" }, "vip"] },
        },
      },
    });
    expect(verdict?.summary).toBe("Booked.");
    expect(verdict?.tags).toEqual(["booked", "vip"]);
  });

  it("is null when there is nothing to say", () => {
    expect(verdictFromAnnotations(null)).toBeNull();
    expect(verdictFromAnnotations({ disposition: "answered" })).toBeNull();
  });
});
