import { describe, expect, it } from "vitest";

import { getActiveNavUrl, getVisibleNavSections } from "../navigation";
const sections = getVisibleNavSections({ isStaff: true, isOrganizationAdmin: false, isSuperadmin: true });
describe("navigation routes", () => {
  it.each([['/tools/42', '/tools'], ['/integrations', '/tools'], ['/marketplace/integrations', '/marketplace/integrations'], ['/marketplace/tools', '/marketplace/tools'], ['/marketplace/skills', '/marketplace/skills'], ['/integrations/apps', '/marketplace/integrations'], ['/numbers', '/telephony-configurations'], ['/verified-numbers', '/telephony-configurations'], ['/missed-calls', '/telephony-configurations'], ['/workflow/42', '/workflow'], ['/model-configurations', '/workflow'], ['/recordings', '/files'], ['/reports', '/usage'], ['/review', '/usage'], ['/analytics', '/usage'], ['/analytics/spend', '/usage'], ['/do-not-call', '/privacy'], ['/partner', '/billing'], ['/superadmin/partners', '/superadmin/partners']])("selects %s in %s", (path, expected) => {
    expect(getActiveNavUrl(path, sections)).toBe(expected);
  });
  it("does not select an unrelated prefix", () => expect(getActiveNavUrl('/workflow-other', sections)).toBeUndefined());
  it("keeps every destination reachable, as a sidebar entry or as a tab under one", () => {
    const items = sections.flatMap(section => section.items);
    const urls = items.map(item => item.url);
    const reachable = items.flatMap(item => [item.url, ...(item.activePaths ?? [])]);
    for (const url of ['/overview','/workflow','/tools','/files','/marketplace/tools','/marketplace/skills','/marketplace/integrations','/contacts','/api-keys','/telephony-configurations','/campaigns','/deploy/connect','/deploy/web-widget','/usage','/billing','/privacy','/settings']) expect(urls).toContain(url);
    // Folded into a tab strip rather than removed: the route still works and
    // still lights the sidebar entry it now lives under.
    for (const url of ['/model-configurations','/recordings','/reports','/partner','/do-not-call','/missed-calls','/review','/analytics','/integrations','/integrations/apps']) {
      expect(urls).not.toContain(url);
      expect(reachable).toContain(url);
    }
    expect(new Set(urls).size).toBe(urls.length);
  });
});
