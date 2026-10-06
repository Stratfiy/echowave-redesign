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
      "Phone numbers",
      "Models",
      "Apps and tools",
      "Knowledge",
      "Channels",
      "Company",
      "Developer",
      "Compliance",
    ]);
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
});
