import { existsSync } from "node:fs";
import { resolve } from "node:path";

import { describe, expect, it } from "vitest";

import { activeShellSection, searchSettings, SHELL_GROUPS, SHELL_SECTIONS, visibleShellSections } from "../sections";

const APP = resolve(process.cwd(), "src/app");
const all = visibleShellSections(() => true, true);

describe("The Settings shell's sections (screen 17)", () => {
  it("groups Personal, Connections, Privacy, Advanced, then the workspace", () => {
    expect(SHELL_GROUPS).toEqual(["Personal", "Connections", "Privacy", "Advanced", "Workspace"]);
    const groups = new Set(SHELL_SECTIONS.map((s) => s.group));
    for (const group of SHELL_GROUPS) expect(groups.has(group)).toBe(true);
    // Workspace-only sections sit under the workspace's own heading.
    const workspace = SHELL_SECTIONS.filter((s) => s.group === "Workspace").map((s) => s.id);
    expect(workspace).toEqual(expect.arrayContaining(["workspace", "team", "company", "compliance"]));
    // Files is one page, reached from the rail; Settings does not list it.
    expect(SHELL_SECTIONS.map((s) => s.title)).not.toContain("Files");
  });

  it.each(SHELL_SECTIONS.map((s) => [s.title, s.mobileHref ?? s.href]))("%s is a page (%s)", (_, href) => {
    expect(existsSync(resolve(APP, ...href.split("/").filter(Boolean), "page.tsx")), href).toBe(true);
  });

  it("hides a section whose switch is off, and admin-only ones from members", () => {
    const member = visibleShellSections((feature) => feature !== "memory_manager", false).map((s) => s.id);
    expect(member).not.toContain("memory");
    expect(member).not.toContain("developer");
    expect(member).toContain("models");
    expect(member).toContain("privacy");
  });

  it.each([
    ["/settings", "account"],
    ["/settings/account", "account"],
    ["/settings/general", "workspace"],
    ["/settings/memory", "memory"],
    ["/marketplace/skills", "skills"],
    ["/do-not-call", "compliance"],
  ])("lights %s as %s", (path, id) => {
    expect(activeShellSection(path, all)).toBe(id);
  });
});

describe("Settings search uses everyday words", () => {
  it.each([
    ["mic", "voice"],
    ["microphone", "voice"],
    ["memory", "memory"],
    ["email", "account"],
    ["dark mode", "account"],
    ["2fa", "privacy"],
    ["delete account", "privacy"],
    ["whatsapp", "channels"],
    ["gst", "company"],
    ["dnd", "compliance"],
    ["hindi", "personalization"],
    ["bookmark", "saved"],
  ])("%s finds %s", (query, section) => {
    const hits = searchSettings(query, all);
    expect(hits.length).toBeGreaterThan(0);
    expect(hits[0].section.id).toBe(section);
  });

  it("finds the section by its own title, case and punctuation aside", () => {
    expect(searchSettings("PRIVACY & security", all)[0].section.id).toBe("privacy");
  });

  it("never offers a section this person cannot open", () => {
    const withoutMemory = visibleShellSections((feature) => feature !== "memory_manager", false);
    expect(searchSettings("memory", withoutMemory).map((h) => h.section.id)).not.toContain("memory");
    expect(searchSettings("webhook", withoutMemory)).toEqual([]);
  });

  it("an empty query is no results, and nonsense says nothing matches", () => {
    expect(searchSettings("   ", all)).toEqual([]);
    expect(searchSettings("zzzqqq", all)).toEqual([]);
  });
});
