"use client";

/**
 * One place for everything to do with phone numbers.
 *
 * Carriers, verification, test numbers and buying a number were four separate
 * entries in the left navigation — a quarter of it — for a set of screens that
 * are one job done in sequence: get verified, then buy a number, then point it
 * at an agent. Presented as peers they read as four unrelated features, and
 * the order they have to be done in was visible only to somebody who already
 * knew it.
 *
 * A tab strip rather than a nested menu because these are steps, not a
 * hierarchy, and because the existing routes keep working unchanged — every
 * deep link, redirect and bookmark still lands where it did.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

const TABS = [
  // Ordered the way the work happens, not alphabetically. A customer arriving
  // for the first time reads this left to right and gets the sequence.
  { href: "/verification", label: "Verification" },
  { href: "/numbers", label: "Get a number" },
  { href: "/telephony-configurations", label: "Carriers & numbers" },
  { href: "/verified-numbers", label: "Test numbers" },
];

export function TelephonyTabs() {
  const pathname = usePathname();

  return (
    <nav
      aria-label="Telephony"
      className="w-full overflow-x-auto border-b border-border px-6"
    >
      <ul className="flex min-w-max gap-1">
        {TABS.map((tab) => {
          // startsWith, so a configuration's own page keeps its tab lit rather
          // than dropping the reader out of the section they are standing in.
          const active =
            pathname === tab.href || pathname.startsWith(`${tab.href}/`);
          return (
            <li key={tab.href}>
              <Link
                href={tab.href}
                aria-current={active ? "page" : undefined}
                className={cn(
                  "-mb-px inline-block border-b-2 px-3 py-2.5 text-sm whitespace-nowrap transition-colors",
                  active
                    ? "border-primary font-medium text-foreground"
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

export default TelephonyTabs;
