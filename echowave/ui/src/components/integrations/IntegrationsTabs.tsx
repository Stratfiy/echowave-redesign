"use client";

/**
 * One place for everything that connects Decibyl to something outside it.
 *
 * Provider keys and Tools were two peers in the left navigation, and Google
 * Calendar lived inside the provider-keys screen with no name for what it
 * was doing there. All three are the same idea — an outside account or
 * service an agent can reach — which is why a competitor product groups them
 * under one "Integrations" label instead of three.
 *
 * A tab strip rather than a merged page, same reasoning as Telephony's: these
 * are peers, not steps, and the existing routes keep working unchanged so
 * every deep link — including the three from the model editor and the
 * Google OAuth callback — still lands where it did.
 */

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

const TABS = [
    { href: "/integrations", label: "Providers" },
    { href: "/tools", label: "Tools" },
];

export function IntegrationsTabs() {
    const pathname = usePathname();

    return (
        <nav
            aria-label="Integrations"
            className="w-full overflow-x-auto border-b border-border px-6"
        >
            <ul className="flex min-w-max gap-1">
                {TABS.map((tab) => {
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

export default IntegrationsTabs;
