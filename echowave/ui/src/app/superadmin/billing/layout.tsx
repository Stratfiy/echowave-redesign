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
        <div className="mx-auto w-full max-w-[1400px] px-6 py-6">
            <header className="mb-6">
                <h1 className="text-xl font-semibold tracking-tight">Billing &amp; usage</h1>
                <p className="mt-0.5 text-sm text-muted-foreground">
                    Cross-account financial and performance data. Times shown in IST.
                </p>
            </header>

            <nav className="mb-6 flex gap-1 border-b" aria-label="Billing sections">
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
                                "-mb-px border-b-2 px-3 py-2 text-sm font-medium transition-colors",
                                active
                                    ? "border-primary text-foreground"
                                    : "border-transparent text-muted-foreground hover:text-foreground",
                            )}
                        >
                            {tab.label}
                        </Link>
                    );
                })}
            </nav>

            {children}
        </div>
    );
}
