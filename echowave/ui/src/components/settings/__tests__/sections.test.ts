import { existsSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { activeSection, SETTINGS_SECTIONS } from "../sections";

const APP = resolve(process.cwd(), "src/app");

describe("Settings' sections", () => {
  it("lists them in the designed order", () => {
    expect(SETTINGS_SECTIONS.map((s) => s.title)).toEqual([
      "General",
      "Team",
      "Daily brief",
      "Notifications",
      "Models",
      "Knowledge",
      "Apps and tools",
      "Channels",
      "Phone numbers",
      "Company",
      "Connections",
      "Decibyl identity",
      "Advanced",
      "Developer",
      "Compliance",
    ]);
  });

  it("puts every section in a group, and General has a page of its own on a phone", () => {
    expect(new Set(SETTINGS_SECTIONS.map((s) => s.group))).toEqual(new Set(["You", "Assistant", "Identity", "Advanced"]));
    const general = SETTINGS_SECTIONS.find((s) => s.id === "general");
    expect(general?.mobileHref).toBe("/settings/general");
    expect(existsSync(resolve(APP, "settings", "general", "page.tsx"))).toBe(true);
    expect(activeSection("/settings/general")).toBe("general");
  });

  it.each(SETTINGS_SECTIONS.map((s) => [s.title, s.href]))("%s is a page (%s)", (_, href) => {
    expect(existsSync(resolve(APP, ...href.split("/").filter(Boolean), "page.tsx")), href).toBe(true);
  });

  it.each([
    ["/settings", "general"],
    ["/settings/team", "team"],
    ["/settings/phone-number", "phone-number"],
    ["/telephony-configurations/4", "phone-number"],
    ["/tools/abc", "apps"],
    ["/do-not-call", "compliance"],
    ["/settings/connections", "connections"],
    ["/settings/identity", "identity"],
    ["/settings/notifications", "notifications"],
  ])("lights %s as %s", (path, id) => {
    expect(activeSection(path)).toBe(id);
  });
});

describe("Identity sections (launch stream identity)", () => {
  it("are behind their own switches", () => {
    const byId = Object.fromEntries(SETTINGS_SECTIONS.map((s) => [s.id, s.flags]));
    expect(byId.connections).toEqual(["identity_connections"]);
    expect(byId.identity).toEqual(["identity_email", "identity_phone"]);
    expect(byId.notifications).toEqual(["identity_notifications"]);
    expect(byId.general).toBeUndefined();
  });
});
