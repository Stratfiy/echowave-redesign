import type { PageTab } from "@/components/layout/PageHeader";
import { DESK_TABS } from "@/components/layout/SectionTabs";

/** The desk's tabs with Activity pointing at Today's own activity (screen
 *  09) while `today_list` is on. The other tabs are the desk's, unchanged. */
export const TODAY_TABS: PageTab[] = DESK_TABS.map((tab) =>
    tab.href === "/usage" ? { href: "/tasks/activity", label: "Activity", prefix: true } : tab,
);
