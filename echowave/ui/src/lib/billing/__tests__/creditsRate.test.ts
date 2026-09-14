import { describe, expect, it } from "vitest";

import { formatCredits, formatCreditsRate, PAISE_PER_CREDIT } from "../format";

describe("formatCreditsRate", () => {
  it("keeps a decimal so small per-minute rates stay comparable", () => {
    // The bug this guards: the integer formatter rounds 128 and 207 paise to
    // "1" and "2", which makes two differently priced slots look one credit
    // apart when they are not.
    expect(formatCreditsRate(128)).toBe("2.6");
    expect(formatCreditsRate(207)).toBe("4.1");
    expect(formatCreditsRate(853)).toBe("17.1");
    expect(formatCredits(128)).toBe("2"); // balances stay whole, rounded down
  });

  it("renders nothing for an unpriced slot rather than a zero", () => {
    expect(formatCreditsRate(null)).toBe("—");
    expect(formatCreditsRate(undefined)).toBe("—");
  });

  it("is fifty paise, the published peg, and a balance rounds down", () => {
    expect(PAISE_PER_CREDIT).toBe(50);
    expect(formatCreditsRate(50)).toBe("1.0");
    expect(formatCredits(1749)).toBe("34");
    expect(formatCredits(250000)).toBe("5,000"); // the Business grant
  });
});
