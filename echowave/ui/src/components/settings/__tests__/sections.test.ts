import { existsSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { activeSection, SETTINGS_SECTIONS, visibleSections } from "../sections";

const APP = resolve(process.cwd(), "src/app");

describe("Settings' sections", () => {
  it("lists them in the designed order", () => {
    expect(SETTINGS_SECTIONS.map((s) => s.title)).toEqual([
      "General",
      "Team",
      "Voice and language",
      "Models",
      "Knowledge",
      "Apps and tools",
      "Channels",
      "Phone numbers",
      "Company",
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
  ])("lights %s as %s", (path, id) => {
    expect(activeSection(path)).toBe(id);
  });

  it("lists Voice and language only while its switch is on", () => {
    const off = visibleSections(() => false).map((s) => s.id);
    expect(off).not.toContain("voice");
    expect(off).toHaveLength(SETTINGS_SECTIONS.length - 1);
    const on = visibleSections((f) => f === "voice_language_settings").map((s) => s.id);
    expect(on).toContain("voice");
    expect(activeSection("/settings/voice")).toBe("voice");
  });
});
