import { describe, expect, it } from "vitest";

import { formatCredits, formatCreditsRate } from "../format";

describe("formatCreditsRate", () => {
  it("keeps a decimal so small per-minute rates stay comparable", () => {
    // The bug this guards: the integer formatter rounds 128 and 207 paise to
    // "1" and "2", which makes two differently priced slots look one credit
    // apart when they are not.
    expect(formatCreditsRate(128)).toBe("1.3");
    expect(formatCreditsRate(207)).toBe("2.1");
    expect(formatCreditsRate(853)).toBe("8.5");
    expect(formatCredits(128)).toBe("1"); // balances stay whole
  });

  it("renders nothing for an unpriced slot rather than a zero", () => {
    expect(formatCreditsRate(null)).toBe("—");
    expect(formatCreditsRate(undefined)).toBe("—");
  });

  it("is 1:1 with rupees, the published peg", () => {
    expect(formatCreditsRate(100)).toBe("1.0");
  });
});
