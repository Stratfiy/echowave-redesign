"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import React, { ReactNode } from "react";

import { cn } from "@/lib/utils";

/**
 * The band that sits between the top bar and a page's content: title on the
 * left, actions on the right, an optional tab row beneath.
 *
 * Every screen was rolling its own — a bare `<h1>` at a different size, with
 * the action buttons floated wherever the page happened to put them — which is
 * why no two pages lined up. This is the one place a page title is styled.
 */

export type PageTab = {
  label: string;
  href: string;
  /** Match the pathname by prefix rather than equality. Needed for sections
   *  whose tab lands on a list that then pushes detail routes underneath it. */
  prefix?: boolean;
  /** Other routes that light this tab. For a screen that is a step inside
   *  another one rather than a peer of it: it has no tab of its own, and the
   *  strip keeps saying where the reader is. */
  also?: string[];
};

/**
 * The one tab a path lights: the most specific match. /billing carries
 * prefix, so /billing/spend matches both Billing and Spend; the longer href
 * wins and only Spend is lit. `also` routes count as matches of their tab.
 */
export function activeTab(tabs: PageTab[], pathname: string): string | undefined {
  let best: { href: string; length: number } | undefined;
  for (const tab of tabs) {
    const routes = [
      ...(tab.prefix ? [tab.href] : []),
      ...(tab.also ?? []),
    ];
    const lengths = [
      ...(pathname === tab.href ? [tab.href.length] : []),
      ...routes.filter((route) => pathname === route || pathname.startsWith(route)).map((route) => route.length),
    ];
    const length = Math.max(-1, ...lengths);
    if (length >= 0 && (!best || length > best.length)) best = { href: tab.href, length };
  }
  return best?.href;
}

interface PageHeaderProps {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  tabs?: PageTab[];
  className?: string;
}

function scrollIntoViewOnce(node: HTMLAnchorElement | null) {
  node?.scrollIntoView?.({ block: "nearest", inline: "nearest" });
}

export function PageTabs({ tabs }: { tabs: PageTab[] }) {
  // Null outside the app router (unit tests render pages bare), and a strip
  // with nothing lit is the right answer there.
  const pathname = usePathname() ?? "";
  const active = activeTab(tabs, pathname);

  return (
    // The strip carries the rule under it, so a page needs only to hand
    // `PageHeader` its tabs to get the underline that separates the header
    // band from the content.
    <nav
      className="w-full overflow-x-auto border-b border-border/70 [scrollbar-width:none] [&::-webkit-scrollbar]:hidden"
      aria-label="Section"
    >
      <ul className="-mb-px flex min-w-max items-center gap-0.5 px-2 sm:gap-1 sm:px-6">
      {tabs.map((tab) => {
        const isActive = tab.href === active;
        return (
          <li key={tab.href}>
            <Link
              href={tab.href}
              aria-current={isActive ? "page" : undefined}
              // On a phone the strip scrolls; the open tab is brought into
              // view rather than left past the edge.
              ref={isActive ? scrollIntoViewOnce : undefined}
              className={cn(
                "-mb-px inline-block border-b-2 px-2.5 py-2.5 text-sm whitespace-nowrap transition-colors sm:px-3",
                // The brand coral, not `--primary`. `--primary` is #171717 —
                // near-black, and a near-black underline on a near-black label
                // does not read as "this one". The section strip already used
                // the accent; this is the same strip now.
                isActive
                  ? "border-[var(--accent-brand)] font-medium text-foreground"
                  : "border-transparent text-muted-foreground hover:text-foreground"
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

export function PageHeader({
  title,
  description,
  actions,
  tabs,
  className,
}: PageHeaderProps) {
  return (
    // On the floor, not a white band over it: the ivory runs from the rail to
    // the content and only cards are white. A bordered white strip here read
    // as a second top bar and split the screen into three tones.
    <div className={cn("page-header app-glass sticky top-0 z-30", className)}>
      {/* Tight on a phone, roomy on a desk. The title, its explanation and a
          five-tab strip were together taking the top third of a 390px screen,
          so the thread they head got a sliver and the reader scrolled before
          they had read anything. Buzz heads a room with one line and gives
          the rest to the messages. The explanation is the part that goes:
          it is orientation for a first visit, and it costs two lines on
          every visit after. */}
      <div className="page-header-inner px-4 pt-3 pb-2 sm:px-6 sm:pt-5 sm:pb-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h1 className="page-header-title truncate text-lg leading-tight text-foreground sm:text-[26px]">{title}</h1>
            {description && (
              <p className="page-header-sub mt-1 hidden text-sm text-muted-foreground sm:block">{description}</p>
            )}
          </div>
          {actions && (
            // `flex-wrap` without `shrink-0`: the container has to be allowed
            // to narrow before its own wrapping can do anything. With
            // `shrink-0` it kept the width of every action on one line, so on a
            // 320px phone three buttons pushed the page 232px sideways and the
            // wrap never fired.
            <div className="flex flex-wrap items-center gap-2">{actions}</div>
          )}
        </div>
      </div>
      {/* Full-bleed: the rule under the strip runs the width of the content
          well, so the header band ends on a line rather than on a gap. The
          gutter is on the tabs themselves. */}
      {tabs && tabs.length > 0 && <PageTabs tabs={tabs} />}
    </div>
  );
}

/**
 * Standard content well. Full-bleed with generous padding rather than a narrow
 * centred column: at 1440 the old `container mx-auto` left roughly half the
 * horizontal space as empty gutter on either side of a 670px column, which is
 * what made populated screens look as sparse as empty ones.
 */
export function PageBody({
  children,
  className,
}: {
  children: ReactNode;
  className?: string;
}) {
  return <div className={cn("w-full px-6 py-6", className)}>{children}</div>;
}

export default PageHeader;
