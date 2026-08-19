"use client";

import Link from "next/link";
import { usePathname } from "next/navigation";
import { useEffect, useState } from "react";

import { getRateCardApiV1AdminBillingRateCardGet } from "@/client/sdk.gen";
import { useAuth } from "@/lib/auth";
import { formatMicrosUsd, formatRateMpaise } from "@/lib/billing/format";
import { cn } from "@/lib/utils";

const TABS = [
    { href: "/superadmin/billing", label: "Overview", exact: true },
    { href: "/superadmin/billing/accounts", label: "Accounts" },
    { href: "/superadmin/billing/calls", label: "Calls" },
    { href: "/superadmin/billing/campaigns", label: "Campaigns" },
    { href: "/superadmin/billing/latency", label: "Latency" },
    { href: "/superadmin/billing/activation", label: "Activation" },
    { href: "/superadmin/billing/tokens", label: "Tokens" },
    { href: "/superadmin/billing/models", label: "Models" },
    { href: "/superadmin/billing/payments", label: "Payments" },
    { href: "/superadmin/billing/realtime", label: "Realtime" },
    { href: "/superadmin/billing/unit-economics", label: "Unit economics" },
    { href: "/superadmin/billing/pricing-inputs", label: "Pricing inputs" },
    { href: "/superadmin/billing/rate-card", label: "Rate card" },
];

/**
 * The current price, read from the rate card rather than hard-coded.
 *
 * It used to be a literal, which was fine while the price lived in a constant.
 * Now that it is editable, a literal would go stale the first time someone
 * changed it — and a header asserting the wrong price is worse than no header.
 *
 * Fetched in the layout, which persists across the tabs below, so this is one
 * request for the whole section rather than one per screen.
 */
function usePriceSummary(): { price: string; pulse: number } | null {
    const { user, loading } = useAuth();
    const [summary, setSummary] = useState<{ price: string; pulse: number } | null>(
        null,
    );

    useEffect(() => {
        if (loading || !user) return;
        let cancelled = false;
        (async () => {
            const result = await getRateCardApiV1AdminBillingRateCardGet({});
            if (cancelled || result.error || !result.data) return;
            const card = result.data as unknown as {
                global_tier: {
                    platform_rate_micros_usd: number | null;
                    platform_rate_mpaise: number | null;
                    pulse_seconds: number;
                } | null;
                fallback: { platform_rate_micros_usd: number; pulse_seconds: number };
            };
            const tier = card.global_tier;
            setSummary({
                price:
                    tier?.platform_rate_mpaise != null
                        ? `${formatRateMpaise(tier.platform_rate_mpaise)}/min`
                        : `${formatMicrosUsd(
                              tier?.platform_rate_micros_usd ??
                                  card.fallback.platform_rate_micros_usd,
                          )}/min`,
                pulse: tier?.pulse_seconds ?? card.fallback.pulse_seconds,
            });
        })();
        return () => {
            cancelled = true;
        };
    }, [loading, user]);

    return summary;
}

export default function BillingDashboardLayout({
    children,
}: {
    children: React.ReactNode;
}) {
    const pathname = usePathname();
    const summary = usePriceSummary();

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
                        figure on every screen below is a consequence of it.
                        Rendered only once the real price has loaded — a
                        placeholder here would be a claim about pricing. */}
                    {summary && (
                        <Link
                            href="/superadmin/billing/rate-card"
                            className="glass-panel rounded-full px-4 py-2 text-xs tracking-[-0.01em] text-muted-foreground transition-colors hover:text-foreground"
                        >
                            <span className="font-medium text-foreground">
                                {summary.price}
                            </span>
                            {" + provider cost "}
                            <span className="font-medium text-[color:var(--brand-amber)]">
                                at cost
                            </span>
                            {", billed in "}
                            <span className="font-medium text-foreground">
                                {summary.pulse}s pulses
                            </span>
                        </Link>
                    )}
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
