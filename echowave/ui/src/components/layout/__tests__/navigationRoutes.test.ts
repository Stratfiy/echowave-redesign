import { describe, expect, it } from "vitest";

import { getVisibleNavSections } from "../navigation";
const sections = getVisibleNavSections({ isStaff: true, isOrganizationAdmin: false, isSuperadmin: true });
describe("navigation routes", () => {
  it("keeps every destination reachable, as a sidebar entry or as a tab under one", () => {
    const items = sections.flatMap(section => section.items);
    const urls = items.map(item => item.url);
    const reachable = items.flatMap(item => [item.url, ...(item.activePaths ?? [])]);
    for (const url of ['/overview','/workflow','/settings/apps','/settings/knowledge','/marketplace','/settings/developer','/settings/phone-number','/campaigns','/deploy/connect','/deploy/web-widget','/usage','/billing','/settings/compliance','/settings']) expect(urls).toContain(url);
    // Folded into a tab strip rather than removed: the route still works and
    // still lights the sidebar entry it now lives under.
    for (const url of ['/recordings','/partner','/do-not-call','/missed-calls','/review','/analytics','/integrations','/contacts','/marketplace/tools','/marketplace/skills','/marketplace/integrations']) {
      expect(urls).not.toContain(url);
      expect(reachable).toContain(url);
    }
    expect(new Set(urls).size).toBe(urls.length);
  });
});
