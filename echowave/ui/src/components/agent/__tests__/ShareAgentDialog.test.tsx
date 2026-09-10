import { describe, expect, it } from "vitest";

import { describeUsage, expiryChoiceFor } from "../ShareAgentDialog";

const link = {
  url: "https://app.decibyl.ai/talk/emb_x",
  token: "emb_x",
  is_active: true,
  expires_at: null,
  daily_minutes_cap: 30,
  minutes_used_today: 12,
};

describe("describeUsage", () => {
  it("says how much of the day's limit is used", () => {
    expect(describeUsage(link)).toBe("12 of 30 min used today");
  });

  it("says when there is no limit", () => {
    expect(describeUsage({ ...link, daily_minutes_cap: null })).toBe("12 min used today, no daily limit");
  });
});

describe("expiryChoiceFor", () => {
  const now = new Date("2026-09-10T10:00:00Z");

  it("is never for a link with no expiry", () => {
    expect(expiryChoiceFor(null, now)).toBe("never");
  });

  it("snaps a stored date to the nearest choice", () => {
    expect(expiryChoiceFor("2026-10-09T09:00:00Z", now)).toBe("30");
    expect(expiryChoiceFor("2026-09-16T10:00:00Z", now)).toBe("7");
    expect(expiryChoiceFor("2026-12-01T10:00:00Z", now)).toBe("90");
  });
});
