import { describe, expect, it } from "vitest";

import { getActiveNavUrl, getVisibleNavSections } from "../navigation";
const sections = getVisibleNavSections({ isStaff: true, isOrganizationAdmin: false });
describe("navigation routes", () => {
  it.each([['/tools/42', '/integrations/apps'], ['/integrations', '/integrations/apps'], ['/numbers', '/telephony-configurations'], ['/verified-numbers', '/telephony-configurations'], ['/missed-calls', '/telephony-configurations'], ['/workflow/42', '/workflow'], ['/model-configurations/7', '/workflow'], ['/recordings', '/files'], ['/reports', '/usage'], ['/do-not-call', '/privacy'], ['/partner', '/billing'], ['/superadmin/partners', '/superadmin/partners']])("selects %s in %s", (path, expected) => {
    expect(getActiveNavUrl(path, sections)).toBe(expected);
  });
  it("does not select an unrelated prefix", () => expect(getActiveNavUrl('/workflow-other', sections)).toBeUndefined());
  it("keeps every destination reachable, as a sidebar entry or as a tab under one", () => {
    const items = sections.flatMap(section => section.items);
    const urls = items.map(item => item.url);
    const reachable = items.flatMap(item => [item.url, ...(item.activePaths ?? [])]);
    for (const url of ['/overview','/workflow','/integrations/apps','/files','/contacts','/api-keys','/telephony-configurations','/campaigns','/deploy/connect','/deploy/web-widget','/analytics','/usage','/billing','/privacy','/settings']) expect(urls).toContain(url);
    // Folded into a tab strip rather than removed: the route still works and
    // still lights the sidebar entry it now lives under.
    for (const url of ['/model-configurations','/recordings','/reports','/partner','/do-not-call','/missed-calls']) {
      expect(urls).not.toContain(url);
      expect(reachable).toContain(url);
    }
    expect(new Set(urls).size).toBe(urls.length);
  });
});
