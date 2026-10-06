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
  CALLS_TABS,
  COMPLIANCE_TABS,
  DESK_TABS,
  DEVELOPER_TABS,
  KNOWLEDGE_TABS,
  MARKETPLACE_TABS,
  TELEPHONY_TABS,
} from "../SectionTabs";

const STRIPS = {
  CALLS_TABS,
  KNOWLEDGE_TABS,
  COMPLIANCE_TABS,
  BILLING_TABS,
  MARKETPLACE_TABS,
  TELEPHONY_TABS,
  DESK_TABS,
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
    expect(BILLING_TABS.map((tab) => tab.href)).toContain("/billing/spend");
    expect(CALLS_TABS.some((tab) => tab.href.includes("spend"))).toBe(false);
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
    expect(DEVELOPER_TABS.map((tab) => tab.href)).toEqual(["/settings/developer", "/deploy/connect"]);
  });

  it("keeps the phone strip in the order the work happens", () => {
    expect(TELEPHONY_TABS.map((tab) => tab.href)).toEqual([
      "/numbers",
      "/settings/phone-number",
      "/verified-numbers",
    ]);
  });

  it("treats verification as a step inside Get a number, not a peer of it", () => {
    // It is drawn as step 1 of Get a number, with its live status. A tab of
    // its own put the same step in two places and let somebody start with
    // the paperwork without seeing what it was for.
    // Its form now opens in place on /numbers, so there is no route to light.
    expect(TELEPHONY_TABS.map((tab) => tab.href)).not.toContain("/verification");
  });

  it("puts the diary, the in-tray and the contact book on one desk", () => {
    // Tasks and Requests were tabs of Decibyl, and Tasks was a third way to
    // reach a door the sidebar already pins. Contacts came from Setup, where
    // it sat beside Campaigns -- but a campaign dialling a list is delivery,
    // and a person you deal with is desk work.
    // Handed over joined them once the timeline could filter to the rows a
    // bot hands back: finished work is desk work too.
    expect(DESK_TABS.map((tab) => tab.href)).toEqual([
        "/tasks",
        "/schedules",
        "/contacts",
        "/deliverables",
    ]);
  });

  it("gives the shop one screen with departments, not four rows", () => {
    // Four rows for one shop read as four unrelated features, and the shop's
    // Tools shared a word with the account's own Your tools.
    expect(MARKETPLACE_TABS.map((tab) => tab.href)).toEqual([
      "/marketplace",
      "/marketplace/tools",
      "/marketplace/skills",
      "/marketplace/integrations",
    ]);
    // /marketplace leads the strip and must not carry prefix, or every
    // department would light it as well as their own.
    expect(MARKETPLACE_TABS.every((tab) => !tab.prefix)).toBe(true);
  });

  it("lets /analytics light its tab from a detail page", () => {
    // /analytics carries prefix: true; nothing under it belongs to another
    // tab since Spend moved to /billing/spend.
    const analytics = CALLS_TABS.find((tab) => tab.href === "/analytics");
    expect(analytics?.prefix).toBe(true);
    expect(CALLS_TABS.some((tab) => tab.href.startsWith("/analytics/"))).toBe(false);
  });
});
