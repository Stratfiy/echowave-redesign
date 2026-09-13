/**
 * The rail's five contexts.
 *
 * The test that matters is the first one. A rail built by listing which
 * destinations belong to each panel is an allowlist, and an allowlist that
 * someone forgets to extend does not raise anything — the new screen simply
 * cannot be reached from the sidebar, with no error and no symptom. That is
 * the silent-absence shape the API notes warn about, applied to navigation.
 * So every item must land somewhere, and anything unplaced lands in Account.
 */

import { describe, expect, it } from "vitest";

import {
  CONTEXT_FALLBACK,
  contextIdForUrl,
  getContextSections,
  getVisibleNavSections,
  NAV_CONTEXTS,
  NAV_SECTIONS,
  STAFF_SECTION,
} from "../navigation";

const ALL_ROLES = { isStaff: true, isOrganizationAdmin: true };

describe("the rail's contexts", () => {
  it("reaches every nav item through exactly one panel", () => {
    const sections = getVisibleNavSections(ALL_ROLES);
    const items = sections.flatMap(section => section.items);
    expect(items.length).toBeGreaterThan(0);

    for (const item of items) {
      const panels = NAV_CONTEXTS.filter(context =>
        getContextSections(context.id, sections).some(section =>
          section.items.some(candidate => candidate.url === item.url),
        ),
      );
      expect(panels.map(p => p.id), `${item.title} (${item.url})`).toHaveLength(1);
    }
  });

  it("files an unplaced destination in Account rather than nowhere", () => {
    expect(contextIdForUrl("/a-screen-nobody-assigned")).toBe(CONTEXT_FALLBACK);
    expect(CONTEXT_FALLBACK).toBe("account");
  });

  it("never lists one url in two contexts", () => {
    const seen = new Map<string, string>();
    for (const context of NAV_CONTEXTS) {
      for (const url of context.urls) {
        expect(seen.get(url), `${url} is in ${seen.get(url)} and ${context.id}`).toBeUndefined();
        seen.set(url, context.id);
      }
    }
  });

  it("has no context whose panel is empty for a full-access user", () => {
    const sections = getVisibleNavSections(ALL_ROLES);
    for (const context of NAV_CONTEXTS) {
      expect(getContextSections(context.id, sections).length, context.id).toBeGreaterThan(0);
    }
  });

  it("puts the staff section behind Account, not its own rail entry", () => {
    const sections = getVisibleNavSections(ALL_ROLES);
    const account = getContextSections("account", sections).flatMap(s => s.items).map(i => i.url);
    for (const item of STAFF_SECTION.items) {
      expect(account, item.title).toContain(item.url);
    }
  });

  it("shows a member no staff destinations in any panel", () => {
    const sections = getVisibleNavSections({ isStaff: false, isOrganizationAdmin: false });
    const reachable = NAV_CONTEXTS.flatMap(context =>
      getContextSections(context.id, sections).flatMap(s => s.items.map(i => i.url)),
    );
    for (const item of STAFF_SECTION.items) {
      expect(reachable).not.toContain(item.url);
    }
  });

  it("keeps Home as the first context and the bots' door", () => {
    expect(NAV_CONTEXTS[0].id).toBe("home");
    expect(NAV_CONTEXTS[0].urls).toContain("/workflow");
  });

  it("does not lose a destination when NAV_SECTIONS grows", () => {
    // The guard restated against the raw source, so it holds even if
    // getVisibleNavSections is later changed to filter more aggressively.
    for (const item of NAV_SECTIONS.flatMap(s => s.items)) {
      expect(NAV_CONTEXTS.some(c => c.id === contextIdForUrl(item.url)), item.title).toBe(true);
    }
  });
});
