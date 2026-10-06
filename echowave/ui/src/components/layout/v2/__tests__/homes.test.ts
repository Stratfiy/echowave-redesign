import { describe, expect, it } from "vitest";

import { activeHome, colleagueState } from "../homes";
import { parseTrial } from "../useRailData";

describe("activeHome", () => {
  it.each([
    ["/overview", "home"],
    ["/company", "company"],
    ["/tasks", "tasks"],
    ["/workflow", "agents"],
    ["/workflow/12/thread", "agents"],
    ["/files", "knowledge"],
    ["/usage", "activity"],
    ["/reports", "activity"],
    ["/campaigns/3", "activity"],
    ["/settings", "settings"],
    ["/billing", "settings"],
    ["/deploy/web-widget", "settings"],
  ])("puts %s under %s", (path, home) => {
    expect(activeHome(path)).toBe(home);
  });

  it("lights nothing on an unknown page", () => {
    expect(activeHome("/start")).toBeUndefined();
  });
});

describe("colleagueState", () => {
  it("reads attention as needs you, even when live", () => {
    expect(colleagueState({ is_live: true, tone: "attention" })).toBe("needs_you");
  });
  it("reads a live agent as live", () => {
    expect(colleagueState({ is_live: true, tone: "working" })).toBe("live");
  });
  it("reads anything else as idle", () => {
    expect(colleagueState({ is_live: false, tone: "paused" })).toBe("idle");
  });
});

describe("parseTrial", () => {
  it("returns null when the plan has no trial block", () => {
    expect(parseTrial({ plans: [] })).toBeNull();
    expect(parseTrial(undefined)).toBeNull();
  });
  it("returns null when the account is not on a trial", () => {
    expect(parseTrial({ trial: { on_trial: false, active: false } })).toBeNull();
  });
  it("reads days left from the trial block", () => {
    expect(parseTrial({ trial: { on_trial: true, active: true, days_left: 11, days: 14 } })).toEqual({
      onTrial: true,
      active: true,
      daysLeft: 11,
      days: 14,
    });
  });
});
