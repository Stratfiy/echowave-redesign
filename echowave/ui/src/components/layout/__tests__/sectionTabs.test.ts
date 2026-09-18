/**
 * The tab strips, checked for the failure they keep having: one destination
 * in two strips, or a destination in none.
 *
 * Both are invisible in review. A route in two strips means the reader sees
 * a different set of peers depending on which door they came through; a
 * route in none is reachable only by URL, which is the same as gone.
 */

import { describe, expect, it } from "vitest";

import {
  BILLING_TABS,
  BOTS_TABS,
  CALLS_TABS,
  COMPLIANCE_TABS,
  DEVELOPER_TABS,
  KNOWLEDGE_TABS,
  TELEPHONY_TABS,
  WORK_TABS,
} from "../SectionTabs";

const STRIPS = {
  CALLS_TABS,
  BOTS_TABS,
  KNOWLEDGE_TABS,
  COMPLIANCE_TABS,
  BILLING_TABS,
  TELEPHONY_TABS,
  WORK_TABS,
};

describe("section tabs", () => {
  it("never puts one destination in two strips", () => {
    const seen = new Map<string, string>();
    const clashes: string[] = [];
    for (const [strip, tabs] of Object.entries(STRIPS)) {
      for (const tab of tabs) {
        const first = seen.get(tab.href);
        if (first) clashes.push(`${tab.href} is in both ${first} and ${strip}`);
        else seen.set(tab.href, strip);
      }
    }
    expect(clashes).toEqual([]);
  });

  it("asks the call questions in the order they get asked, and no more", () => {
    expect(CALLS_TABS.map((tab) => tab.href)).toEqual([
      "/usage",
      "/missed-calls",
      "/review",
      "/analytics",
    ]);
  });

  it("files Spend under Billing, where the balance is", () => {
    expect(BILLING_TABS.map((tab) => tab.href)).toContain("/analytics/spend");
    expect(CALLS_TABS.map((tab) => tab.href)).not.toContain("/analytics/spend");
  });

  it("files Missed calls with the calls, not with buying a number", () => {
    // It is the only record of a caller a working number refused, not a step
    // in getting set up. Reading it as the fifth thing to do before you can
    // take calls was exactly backwards.
    expect(CALLS_TABS.map((tab) => tab.href)).toContain("/missed-calls");
    expect(TELEPHONY_TABS.map((tab) => tab.href)).not.toContain("/missed-calls");
  });

  it("puts the two developer screens a tab apart", () => {
    // They were two sidebar rows that reached each other only through a
    // button in the corner of one of them.
    expect(DEVELOPER_TABS.map((tab) => tab.href)).toEqual(["/api-keys", "/deploy/connect"]);
  });

  it("keeps the phone strip in the order the work happens", () => {
    expect(TELEPHONY_TABS.map((tab) => tab.href)).toEqual([
      "/verification",
      "/numbers",
      "/telephony-configurations",
      "/verified-numbers",
    ]);
  });

  it("gives the schedules and the board one entry, out of the assistant", () => {
    // Both were tabs of Decibyl, and Tasks was a third way to reach a door
    // the sidebar already pins. The assistant is a colleague you talk to,
    // not a container for the workspace's screens.
    expect(WORK_TABS.map((tab) => tab.href)).toEqual(["/tasks", "/requests"]);
  });

  it("lets /analytics light its tab from a detail page without stealing /analytics/spend", () => {
    // /analytics carries prefix: true, so /analytics/spend would light it too
    // if Spend were still a sibling. It is not -- it is in another strip
    // entirely, and the two strips never appear at once.
    const analytics = CALLS_TABS.find((tab) => tab.href === "/analytics");
    expect(analytics?.prefix).toBe(true);
    expect(CALLS_TABS.some((tab) => tab.href.startsWith("/analytics/"))).toBe(false);
  });
});
