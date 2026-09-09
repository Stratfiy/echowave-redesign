"use client";

/**
 * A tab strip for destinations that share one sidebar entry.
 *
 * The sidebar names a job — Calls, Compliance, Knowledge base, Billing — and
 * the screens that make up that job sit a tab apart from each other rather
 * than each taking a row in the navigation. The routes are unchanged: every
 * deep link and bookmark still lands where it did. Same treatment as the
 * telephony strip, which is the pattern this generalises.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

export type SectionTab = { href: string; label: string };

export const CALLS_TABS: SectionTab[] = [
  { href: "/usage", label: "Calls" },
  { href: "/reports", label: "Daily reports" },
];

export const KNOWLEDGE_TABS: SectionTab[] = [
  { href: "/files", label: "Documents" },
  { href: "/recordings", label: "Audio clips" },
];

export const COMPLIANCE_TABS: SectionTab[] = [
  { href: "/privacy", label: "Privacy" },
  { href: "/do-not-call", label: "Do not call" },
];

export const BILLING_TABS: SectionTab[] = [
  { href: "/billing", label: "Billing" },
  { href: "/partner", label: "Partner programme" },
];

export function SectionTabs({ tabs, label }: { tabs: SectionTab[]; label: string }) {
  // Null outside the app router (unit tests render pages bare), and a strip
  // with nothing lit is the right answer there.
  const pathname = usePathname() ?? "";

  return (
    <nav aria-label={label} className="w-full overflow-x-auto border-b border-border bg-card px-6">
      <ul className="flex min-w-max gap-1">
        {tabs.map((tab) => {
          // startsWith, so a detail page keeps its tab lit rather than dropping
          // the reader out of the section they are standing in.
          const active = pathname === tab.href || pathname.startsWith(`${tab.href}/`);
          return (
            <li key={tab.href}>
              <Link
                href={tab.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "-mb-px inline-block border-b-2 px-3 py-2.5 text-sm whitespace-nowrap transition-colors",
                  active
                    ? "border-[var(--accent-brand)] font-medium text-foreground"
                    : "border-transparent text-muted-foreground hover:text-foreground",
                )}
              >
                {tab.label}
              </Link>
            </li>
          );
        })}
      </ul>
    </nav>
  );
}

export default SectionTabs;
