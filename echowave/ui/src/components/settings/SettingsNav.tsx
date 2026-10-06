"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

import { activeSection, SETTINGS_SECTIONS } from "./sections";

/**
 * The sections of Settings, down the left the way ChatGPT's settings list
 * them; a row that scrolls sideways on a phone.
 */
export function SettingsNav() {
  const pathname = usePathname() ?? "";
  const current = activeSection(pathname);
  return (
    <nav aria-label="Settings sections" className="shrink-0 md:w-48">
      <ul className="flex gap-1 overflow-x-auto px-4 pt-4 md:flex-col md:gap-px md:overflow-visible md:px-3 md:pt-6">
        {SETTINGS_SECTIONS.map((section) => (
          <li key={section.id} className="shrink-0">
            <Link
              href={section.href}
              aria-current={current === section.id ? "page" : undefined}
              className={cn(
                "block whitespace-nowrap rounded-[10px] px-2.5 py-1.5 text-sm text-foreground transition-colors hover:bg-[var(--line)]",
                current === section.id && "bg-[var(--line)] font-medium",
              )}
            >
              {section.title}
            </Link>
          </li>
        ))}
      </ul>
    </nav>
  );
}
