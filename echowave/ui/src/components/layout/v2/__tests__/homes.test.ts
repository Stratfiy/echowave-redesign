import { describe, expect, it } from "vitest";

import { activeHome, colleagueState, HOMES } from "../homes";
import { parseTrial } from "../useRailData";

describe("activeHome", () => {
  it("has Chat, Today and Files, and Studio only behind its flag", () => {
    expect(HOMES.filter((h) => !h.flag).map((h) => h.title)).toEqual(["Chat", "Today", "Files"]);
  });

  it.each(["/settings/knowledge", "/recordings"])("lights Files on %s", (path) => {
    expect(activeHome(path)).toBe("files");
  });

  it.each([
    ["/overview", "chat"],
    ["/workflow/12/thread", "chat"],
    ["/channels/4", "chat"],
    ["/tasks", "today"],
    ["/schedules", "today"],
    ["/usage", "today"],
    ["/activity/usage", "today"],
    ["/reports", "today"],
    ["/campaigns/3", "today"],
  ])("puts %s under %s", (path, home) => {
    expect(activeHome(path)).toBe(home);
  });

  it.each(["/settings", "/billing", "/settings/models"])("lights no home on %s: Settings is in the profile menu", (path) => {
    expect(activeHome(path)).toBeUndefined();
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
