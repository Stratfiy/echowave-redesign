"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";

import { cn } from "@/lib/utils";

const TABS = [
    { href: "/superadmin/billing", label: "Overview", exact: true },
    { href: "/superadmin/billing/accounts", label: "Accounts" },
    { href: "/superadmin/billing/calls", label: "Calls" },
    { href: "/superadmin/billing/campaigns", label: "Campaigns" },
    { href: "/superadmin/billing/latency", label: "Latency" },
];

export default function BillingDashboardLayout({
    children,
}: {
    children: React.ReactNode;
}) {
    const pathname = usePathname();

    return (
        <div className="glass-canvas min-h-full">
            <div className="mx-auto w-full max-w-[1400px] px-6 pb-10 pt-8">
                <header className="mb-6 flex flex-wrap items-end justify-between gap-4">
                    <div>
                        <h1 className="text-[2.125rem] font-semibold leading-[1.1] tracking-[-0.045em] text-foreground">
                            Billing &amp; usage
                        </h1>
                        <p className="mt-1.5 max-w-xl text-[0.9375rem] leading-relaxed tracking-[-0.011em] text-muted-foreground">
                            Cross-account financial and performance data. Times shown in IST.
                        </p>
                    </div>

                    {/* Where the money actually comes from, stated once. Every
                        figure on every screen below is a consequence of it. */}
                    <p className="glass-panel rounded-full px-4 py-2 text-xs tracking-[-0.01em] text-muted-foreground">
                        <span className="font-medium text-foreground">Platform fee</span>
                        {" + provider cost "}
                        <span className="font-medium text-[color:var(--brand-amber)]">
                            at cost
                        </span>
                    </p>
                </header>

                <nav
                    className="glass-nav mb-7 inline-flex flex-wrap gap-1 p-1.5"
                    aria-label="Billing sections"
                >
                    {TABS.map((tab) => {
                        const active = tab.exact
                            ? pathname === tab.href
                            : pathname.startsWith(tab.href);
                        return (
                            <Link
                                key={tab.href}
                                href={tab.href}
                                aria-current={active ? "page" : undefined}
                                className={cn(
                                    "rounded-full px-4 py-1.5 text-sm font-medium tracking-[-0.014em] transition-all duration-200",
                                    active
                                        ? "glass-nav-active"
                                        : "text-muted-foreground hover:bg-foreground/[0.05] hover:text-foreground",
                                )}
                            >
                                {tab.label}
                            </Link>
                        );
                    })}
                </nav>

                {children}
            </div>
        </div>
    );
}
