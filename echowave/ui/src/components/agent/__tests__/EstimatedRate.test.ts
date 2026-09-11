import { describe, expect, it } from "vitest";

import { ratePerMinute } from "../EstimatedRate";

describe("ratePerMinute", () => {
  it("is the whole minute from the model row", () => {
    expect(ratePerMinute({ cost: { total_paise_per_minute: 556, unpriced: [] } })).toBe(556);
  });

  it("is nothing when a line is unpriced", () => {
    // A total missing its largest line is not a smaller price, it is a wrong
    // one, and nothing beats a wrong number beside the Test button.
    expect(
      ratePerMinute({ cost: { total_paise_per_minute: 120, unpriced: ["tts:rumik"] } }),
    ).toBeNull();
  });

  it("is nothing when there is no cost at all", () => {
    expect(ratePerMinute({ cost: null })).toBeNull();
    expect(ratePerMinute(null)).toBeNull();
  });
});
