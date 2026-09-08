import { describe, expect, it } from "vitest";

import { getActiveNavUrl, getVisibleNavSections } from "../navigation";
const sections = getVisibleNavSections({ isStaff: true, isOrganizationAdmin: false });
describe("navigation routes", () => {
  it.each([['/tools/42', '/integrations/apps'], ['/integrations', '/integrations/apps'], ['/numbers', '/telephony-configurations'], ['/verified-numbers', '/telephony-configurations'], ['/workflow/42', '/workflow'], ['/superadmin/partners', '/superadmin/partners']])("selects %s in %s", (path, expected) => {
    expect(getActiveNavUrl(path, sections)).toBe(expected);
  });
  it("does not select an unrelated prefix", () => expect(getActiveNavUrl('/workflow-other', sections)).toBeUndefined());
  it("preserves every existing destination and exposes missed calls and settings", () => {
    const urls = sections.flatMap(section => section.items.map(item => item.url));
    for (const url of ['/overview','/workflow','/model-configurations','/integrations/apps','/files','/contacts','/recordings','/api-keys','/telephony-configurations','/campaigns','/deploy/connect','/deploy/web-widget','/analytics','/usage','/reports','/billing','/partner','/privacy','/do-not-call','/missed-calls','/settings']) expect(urls).toContain(url);
    expect(new Set(urls).size).toBe(urls.length);
  });
});
