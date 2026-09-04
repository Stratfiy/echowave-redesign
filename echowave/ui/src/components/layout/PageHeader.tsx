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
};

interface PageHeaderProps {
  title: ReactNode;
  description?: ReactNode;
  actions?: ReactNode;
  tabs?: PageTab[];
  className?: string;
}

export function PageTabs({ tabs }: { tabs: PageTab[] }) {
  const pathname = usePathname();

  return (
    <nav className="-mb-px flex items-center gap-1 overflow-x-auto" aria-label="Section">
      {tabs.map((tab) => {
        const active = tab.prefix
          ? pathname.startsWith(tab.href)
          : pathname === tab.href;
        return (
          <Link
            key={tab.href}
            href={tab.href}
            aria-current={active ? "page" : undefined}
            className={cn(
              "whitespace-nowrap border-b-2 px-3 py-2.5 text-sm transition-colors",
              active
                ? "border-primary font-medium text-foreground"
                : "border-transparent text-muted-foreground hover:border-border hover:text-foreground"
            )}
          >
            {tab.label}
          </Link>
        );
      })}
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
    <div className={cn("border-b border-border bg-card", className)}>
      <div className="px-6 pt-5 pb-4">
        <div className="flex flex-wrap items-start justify-between gap-3">
          <div className="min-w-0">
            <h1 className="truncate text-[26px] leading-tight text-foreground">{title}</h1>
            {description && (
              <p className="mt-1 text-sm text-muted-foreground">{description}</p>
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
      {tabs && tabs.length > 0 && (
        <div className="px-6">
          <PageTabs tabs={tabs} />
        </div>
      )}
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
