"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { useAccessRoles } from "@/hooks/useAccessRoles";
import { useFeature } from "@/lib/features";
import { cn } from "@/lib/utils";

/**
 * The staff console's own strip of destinations (ADMIN-2). Before it, the
 * console was a hub of cards and every other screen was reached by knowing
 * its path. Superadmin only: support staff use the KYC queue and nothing else,
 * so a strip of links they would be refused is not shown to them.
 */
export const SUPERADMIN_LINKS: Array<{ href: string; label: string; exact?: boolean }> = [
    { href: "/superadmin", label: "Console", exact: true },
    { href: "/superadmin/billing/accounts", label: "Accounts" },
    { href: "/superadmin/audit", label: "Audit log" },
    { href: "/superadmin/system", label: "System" },
    { href: "/superadmin/flags", label: "Feature flags" },
    { href: "/superadmin/runs", label: "Runs" },
    { href: "/superadmin/invites", label: "Invites" },
    { href: "/superadmin/verification", label: "KYC queue" },
    { href: "/superadmin/billing", label: "Billing", exact: true },
];

export function isActive(pathname: string, href: string, exact?: boolean): boolean {
    if (exact) return pathname === href;
    return pathname === href || pathname.startsWith(`${href}/`);
}

/** The support inbox (screen 32), listed while `support_inbox` is on. */
export const SUPPORT_LINK: (typeof SUPERADMIN_LINKS)[number] = { href: "/superadmin/support", label: "Support" };

export function SuperadminNav() {
    const roles = useAccessRoles();
    const pathname = usePathname() ?? "";
    const supportInbox = useFeature("support_inbox");
    if (!roles.loaded || roles.staffRole !== "superadmin") return null;
    const links = supportInbox ? [...SUPERADMIN_LINKS, SUPPORT_LINK] : SUPERADMIN_LINKS;
    return (
        <nav aria-label="Staff console" className="border-b border-border">
            <ul className="container mx-auto flex max-w-6xl gap-1 overflow-x-auto px-4 py-2 text-sm">
                {links.map((link) => {
                    const active = isActive(pathname, link.href, link.exact);
                    return (
                        <li key={link.href}>
                            <Link
                                href={link.href}
                                aria-current={active ? "page" : undefined}
                                className={cn(
                                    "block whitespace-nowrap rounded-md px-3 py-1.5 hover:bg-muted",
                                    active ? "bg-muted font-medium text-foreground" : "text-muted-foreground",
                                )}
                            >
                                {link.label}
                            </Link>
                        </li>
                    );
                })}
            </ul>
        </nav>
    );
}
