/**
 * The public marketplace: every listed role, no account needed.
 *
 * Browsing is open and hiring is what an account buys — the shape Grok Bot's
 * marketplace settled on in August 2026. Every word on a card is the role's
 * own; this page adds only the chrome around it.
 */

import type { Metadata } from "next";
import Link from "next/link";
import { redirect } from "next/navigation";

import { signedInAgentsDestination } from "@/lib/agentsRoute";
import { getServerUser } from "@/lib/auth/server";
import logger from "@/lib/logger";
import { fetchShelf } from "@/lib/publicMarketplace";

export const metadata: Metadata = {
    title: "Marketplace · Decibyl",
    description: "Agents you can add, what each one does, and what it will ask you before it starts.",
};

type Props = { searchParams: Promise<{ q?: string; job?: string }> };

export default async function PublicMarketplacePage({ searchParams }: Props) {
    const { q, job } = await searchParams;

    // Signed in, "Agents" means your own agents: the sidebar's row of that
    // name is /workflow, and this page has no rail to get back to it. See
    // lib/agentsRoute.ts. A failed auth lookup shows the public page, which
    // is what a visitor would see anyway.
    let signedIn = false;
    try {
        signedIn = Boolean(await getServerUser());
    } catch (error) {
        logger.error("[agents] could not tell whether the visitor is signed in", error);
    }
    if (signedIn) redirect(signedInAgentsDestination({ q, job }));

    const shelf = await fetchShelf({ q, job });

    return (
        <main className="mx-auto w-full max-w-5xl px-6 py-12">
            <p className="text-xs font-semibold uppercase tracking-wider text-[var(--accent-brand)]">Decibyl</p>
            <h1 className="mt-2 text-3xl font-semibold tracking-tight">Marketplace</h1>

            <form className="mt-6 flex gap-2" action="/agents" method="get">
                <input
                    type="search"
                    name="q"
                    defaultValue={q ?? ""}
                    placeholder="What do you need done?"
                    aria-label="Search agents"
                    className="h-10 flex-1 rounded-md border border-border bg-background px-3 text-sm"
                />
                {job && <input type="hidden" name="job" value={job} />}
                <button type="submit" className="h-10 rounded-md bg-[var(--accent-brand)] px-4 text-sm font-medium text-white">
                    Search
                </button>
            </form>

            {shelf && shelf.jobs.length > 0 && (
                <nav className="mt-4 flex flex-wrap gap-2" aria-label="Filter by job">
                    <Link
                        href={q ? `/agents?q=${encodeURIComponent(q)}` : "/agents"}
                        className={`rounded-full border px-3 py-1 text-xs ${!job ? "border-[var(--accent-brand)] text-[var(--accent-brand)]" : "border-border text-muted-foreground"}`}
                    >
                        All
                    </Link>
                    {shelf.jobs.map((name) => (
                        <Link
                            key={name}
                            href={`/agents?job=${encodeURIComponent(name)}${q ? `&q=${encodeURIComponent(q)}` : ""}`}
                            className={`rounded-full border px-3 py-1 text-xs ${job === name ? "border-[var(--accent-brand)] text-[var(--accent-brand)]" : "border-border text-muted-foreground"}`}
                        >
                            {name}
                        </Link>
                    ))}
                </nav>
            )}

            {!shelf ? (
                <p className="mt-10 text-sm text-destructive" role="alert">
                    The marketplace could not be loaded just now. Try again in a moment.
                </p>
            ) : shelf.packs.length === 0 ? (
                <p className="mt-10 text-sm text-muted-foreground">Nothing matches that yet.</p>
            ) : (
                <ul className="mt-8 grid gap-4 sm:grid-cols-2 lg:grid-cols-3">
                    {shelf.packs.map((pack) => (
                        <li key={pack.slug}>
                            <Link
                                href={`/agents/${pack.slug}`}
                                className="flex h-full flex-col rounded-lg border border-border p-5 transition-colors hover:border-[var(--accent-brand)]"
                            >
                                <span className="text-xs text-muted-foreground">{pack.job}</span>
                                <span className="mt-1 text-base font-semibold">{pack.name}</span>
                                <span className="mt-2 line-clamp-3 text-sm text-muted-foreground">{pack.summary}</span>
                                <span className="mt-auto flex flex-wrap gap-1 pt-4">
                                    {pack.badges.map((badge) => (
                                        <span key={badge} className="rounded bg-muted px-2 py-0.5 text-[11px]">
                                            {badge}
                                        </span>
                                    ))}
                                </span>
                            </Link>
                        </li>
                    ))}
                </ul>
            )}
        </main>
    );
}
