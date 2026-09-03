"use client";

import { CircleHelp, LogOut, Menu, Search, Settings } from "lucide-react";
import { useRouter } from "next/navigation";
import React, { useEffect, useMemo, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  DropdownMenu,
  DropdownMenuContent,
  DropdownMenuItem,
  DropdownMenuLabel,
  DropdownMenuSeparator,
  DropdownMenuTrigger,
} from "@/components/ui/dropdown-menu";
import { Input } from "@/components/ui/input";
import { useSidebar } from "@/components/ui/sidebar";
import { Tooltip, TooltipContent, TooltipTrigger } from "@/components/ui/tooltip";
import { type AccessRoles, useAccessRoles } from "@/hooks/useAccessRoles";
import type { LocalUser } from "@/lib/auth";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

import { CurrentOrganization } from "./CurrentOrganization";
import { NAV_SECTIONS, type SidebarNavItem, STAFF_SECTION } from "./navigation";

/**
 * Global search over the app's own destinations.
 *
 * Deliberately client-side and scoped to navigation: there is no cross-entity
 * search endpoint on the backend, and a box that looks like it searches your
 * calls but silently only matches page titles is worse than one that says what
 * it does. The placeholder says "Search pages" for that reason.
 */
function useNavSearch(query: string, roles: AccessRoles) {
  return useMemo(() => {
    const q = query.trim().toLowerCase();
    if (!q) return [];
    // The search index is a second door to every destination, so it has to
    // apply the same rules the sidebar does. It did not: STAFF_SECTION was
    // concatenated unconditionally, so a customer typing "admin", "kyc" or
    // "approve" was offered the staff review queue — the exact thing
    // navigation.ts says is worse than making a reviewer reload. A hidden
    // sidebar entry is not hidden if the search box still hands it over.
    const sections = roles.isStaff ? [...NAV_SECTIONS, STAFF_SECTION] : NAV_SECTIONS;
    const all: SidebarNavItem[] = sections
      .flatMap((s) => s.items)
      .filter((item) => !item.requiresOrganizationAdmin || roles.isOrganizationAdmin);
    return all
      .map((item) => {
        const title = item.title.toLowerCase();
        const kw = (item.keywords ?? []).join(" ");
        // Rank: title prefix beats title substring beats a keyword hit, so
        // typing "bil" puts Billing first rather than whatever matched first.
        let score = -1;
        if (title.startsWith(q)) score = 0;
        else if (title.includes(q)) score = 1;
        else if (kw.includes(q)) score = 2;
        return { item, score };
      })
      .filter((r) => r.score >= 0)
      .sort((a, b) => a.score - b.score)
      .slice(0, 7)
      .map((r) => r.item);
  }, [query, roles.isStaff, roles.isOrganizationAdmin]);
}

function GlobalSearch() {
  const router = useRouter();
  const [query, setQuery] = useState("");
  const [open, setOpen] = useState(false);
  const [active, setActive] = useState(0);
  const wrapRef = useRef<HTMLDivElement>(null);
  const inputRef = useRef<HTMLInputElement>(null);
  const roles = useAccessRoles();
  const results = useNavSearch(query, roles);

  useEffect(() => setActive(0), [query]);

  // Close on outside click. Without this the panel survives a click on the
  // page behind it and hangs over whatever the user was trying to reach.
  useEffect(() => {
    if (!open) return;
    const onDown = (e: MouseEvent) => {
      if (wrapRef.current && !wrapRef.current.contains(e.target as Node)) setOpen(false);
    };
    document.addEventListener("mousedown", onDown);
    return () => document.removeEventListener("mousedown", onDown);
  }, [open]);

  // "/" focuses search, the way it does in monday and most dense web apps —
  // but never while the user is already typing into something else.
  useEffect(() => {
    const onKey = (e: KeyboardEvent) => {
      if (e.key !== "/") return;
      const el = document.activeElement;
      const tag = el?.tagName;
      if (tag === "INPUT" || tag === "TEXTAREA" || (el as HTMLElement)?.isContentEditable) return;
      e.preventDefault();
      inputRef.current?.focus();
    };
    document.addEventListener("keydown", onKey);
    return () => document.removeEventListener("keydown", onKey);
  }, []);

  const go = (url: string) => {
    setOpen(false);
    setQuery("");
    router.push(url);
  };

  const onKeyDown = (e: React.KeyboardEvent) => {
    if (!results.length) return;
    if (e.key === "ArrowDown") {
      e.preventDefault();
      setActive((i) => (i + 1) % results.length);
    } else if (e.key === "ArrowUp") {
      e.preventDefault();
      setActive((i) => (i - 1 + results.length) % results.length);
    } else if (e.key === "Enter") {
      e.preventDefault();
      go(results[active].url);
    } else if (e.key === "Escape") {
      setOpen(false);
    }
  };

  return (
    <div ref={wrapRef} className="relative w-full max-w-xl">
      <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
      <Input
        ref={inputRef}
        value={query}
        onChange={(e) => {
          setQuery(e.target.value);
          setOpen(true);
        }}
        onFocus={() => setOpen(true)}
        onKeyDown={onKeyDown}
        placeholder="Search pages…"
        aria-label="Search pages"
        className="h-9 rounded-full border-transparent bg-muted pl-9 pr-3 text-sm shadow-none focus-visible:border-input focus-visible:bg-card"
      />
      {open && query.trim() !== "" && (
        <div className="absolute left-0 right-0 top-11 z-50 overflow-hidden rounded-xl border border-border bg-popover py-1 shadow-[var(--shadow-raised)]">
          {results.length === 0 ? (
            <p className="px-3 py-3 text-sm text-muted-foreground">No matching page.</p>
          ) : (
            results.map((item, i) => {
              const Icon = item.icon;
              return (
                <button
                  key={item.url}
                  type="button"
                  onMouseEnter={() => setActive(i)}
                  onClick={() => go(item.url)}
                  className={cn(
                    "flex w-full items-center gap-3 px-3 py-2 text-left text-sm",
                    i === active ? "bg-accent text-accent-foreground" : "text-foreground"
                  )}
                >
                  <Icon className="h-4 w-4 shrink-0 text-muted-foreground" />
                  <span className="truncate">{item.title}</span>
                </button>
              );
            })
          )}
        </div>
      )}
    </div>
  );
}

/**
 * The desktop top bar.
 *
 * Before this the app had no top chrome at all on desktop — the header that
 * existed was `md:hidden` and carried only the drawer toggle — so every screen
 * opened straight onto its own content with nothing above it. That is the
 * single biggest structural difference from monday, which puts search and
 * account controls in a persistent bar across the top of every board.
 */
export function TopBar() {
  const { toggleSidebar } = useSidebar();
  const { user, logout, provider } = useAuth();
  const router = useRouter();

  const displayIdentity =
    user?.displayName ||
    (user as { primaryEmail?: string } | undefined)?.primaryEmail ||
    (user as LocalUser | undefined)?.email ||
    "";
  const initials =
    displayIdentity
      .split(/[\s@]/)
      .filter(Boolean)
      .slice(0, 2)
      .map((s: string) => s[0]?.toUpperCase())
      .join("") || "U";

  return (
    <header className="sticky top-0 z-40 flex h-14 shrink-0 items-center gap-3 border-b border-border bg-card px-4">
      <Button
        variant="ghost"
        size="icon"
        onClick={toggleSidebar}
        aria-label="Toggle navigation"
        className="md:hidden"
      >
        <Menu className="h-5 w-5" />
      </Button>

      <div className="flex min-w-0 flex-1 items-center">
        <GlobalSearch />
      </div>

      <div className="flex shrink-0 items-center gap-1">
        {/* Before the account menu, not inside it: which organization you are
            acting as is context for everything on the page, not a setting you
            occasionally open a menu to check. */}
        <CurrentOrganization />

        <Tooltip>
          <TooltipTrigger asChild>
            <Button variant="ghost" size="icon" asChild className="rounded-full">
              <a
                href="https://docs.decibyl.ai"
                target="_blank"
                rel="noopener noreferrer"
                aria-label="Documentation"
              >
                <CircleHelp className="h-[18px] w-[18px]" />
              </a>
            </Button>
          </TooltipTrigger>
          <TooltipContent side="bottom">
            <p>Documentation</p>
          </TooltipContent>
        </Tooltip>

        <DropdownMenu>
          <DropdownMenuTrigger asChild>
            <Button
              variant="ghost"
              size="icon"
              aria-label="Account"
              className="h-8 w-8 rounded-full border border-border bg-muted text-xs font-medium hover:bg-accent"
            >
              {initials}
            </Button>
          </DropdownMenuTrigger>
          <DropdownMenuContent align="end" className="w-56">
            <DropdownMenuLabel className="font-normal">
              <div className="flex flex-col space-y-1">
                {user?.displayName && (
                  <p className="text-sm font-medium">{user.displayName}</p>
                )}
                {displayIdentity && (
                  <p className="truncate text-xs text-muted-foreground">{displayIdentity}</p>
                )}
              </div>
            </DropdownMenuLabel>
            <DropdownMenuSeparator />
            {provider === "stack" && (
              <DropdownMenuItem
                onClick={() => router.push("/handler/account-settings")}
                className="cursor-pointer"
              >
                <Settings className="mr-2 h-4 w-4" />
                Account settings
              </DropdownMenuItem>
            )}
            <DropdownMenuItem onClick={() => router.push("/settings")} className="cursor-pointer">
              <Settings className="mr-2 h-4 w-4" />
              Platform Settings
            </DropdownMenuItem>
            <DropdownMenuItem onClick={() => logout()} className="cursor-pointer">
              <LogOut className="mr-2 h-4 w-4" />
              Sign out
            </DropdownMenuItem>
          </DropdownMenuContent>
        </DropdownMenu>
      </div>
    </header>
  );
}

export default TopBar;
