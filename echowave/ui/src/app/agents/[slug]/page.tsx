/**
 * One role, readable in full before anybody signs up: what it does, how its
 * work is laid out, what it will ask you, which apps it can use, and what it
 * will never do. Every word about the role is the role's own.
 *
 * The prompt text is not here. Publishing it is a decision, not a default,
 * and a public page is cached and indexed whatever is decided later.
 */

import type { Metadata } from "next";
import Link from "next/link";
import { notFound } from "next/navigation";

import { connectorsOf, factsOf, fetchPack, stepKindLabel } from "@/lib/publicMarketplace";

import { HireButton } from "./HireButton";

type Props = { params: Promise<{ slug: string }> };

export async function generateMetadata({ params }: Props): Promise<Metadata> {
    const { slug } = await params;
    const pack = await fetchPack(slug);
    if (!pack) return { title: "Marketplace · Decibyl" };
    return { title: `${pack.card.name} · Decibyl`, description: pack.card.summary };
}

function Section({ title, children }: { title: string; children: React.ReactNode }) {
    return (
        <section className="border-t border-border pt-6">
            <h2 className="text-base font-semibold">{title}</h2>
            <div className="mt-3 text-sm leading-relaxed text-muted-foreground">{children}</div>
        </section>
    );
}

export default async function PublicPackPage({ params }: Props) {
    const { slug } = await params;
    const pack = await fetchPack(slug);
    if (!pack) notFound();

    const { card } = pack;
    const facts = factsOf(pack);
    const connectors = connectorsOf(pack);

    return (
        <main className="mx-auto w-full max-w-3xl space-y-8 px-6 py-12">
            <div>
                <Link href="/agents" className="text-xs text-muted-foreground hover:underline">
                    ← Marketplace
                </Link>
                <p className="mt-4 text-xs text-muted-foreground">{card.job}</p>
                <h1 className="mt-1 text-3xl font-semibold tracking-tight">{card.name}</h1>
                <p className="mt-3 max-w-prose text-sm leading-relaxed text-muted-foreground">{card.summary}</p>
                <div className="mt-4 flex flex-wrap gap-1">
                    {card.badges.map((badge) => (
                        <span key={badge} className="rounded bg-muted px-2 py-0.5 text-[11px]">
                            {badge}
                        </span>
                    ))}
                    {card.languages.map((language) => (
                        <span key={language} className="rounded border border-border px-2 py-0.5 text-[11px] uppercase">
                            {language}
                        </span>
                    ))}
                </div>
                <div className="mt-6 flex flex-wrap items-center gap-3">
                    <HireButton templateId={pack.template_id} name={card.name} />
                    {card.demo_url && (
                        <a href={card.demo_url} className="text-sm underline">
                            Try it first
                        </a>
                    )}
                </div>
                <p className="mt-2 text-xs text-muted-foreground">By {card.publisher.name}</p>
            </div>

            {pack.outline.length > 0 && (
                <Section title="How it works">
                    <ol className="space-y-2">
                        {pack.outline.map((step, index) => (
                            <li key={`${step.name}-${index}`} className="flex gap-3">
                                <span className="w-20 shrink-0 text-xs uppercase tracking-wide">{stepKindLabel(step.kind, pack.speaks)}</span>
                                <span className="text-foreground">{step.name}</span>
                            </li>
                        ))}
                    </ol>
                    {pack.runs && <p className="mt-3">Runs {pack.runs}.</p>}
                </Section>
            )}

            {facts.length > 0 && (
                <Section title="What it will ask you">
                    <ul className="space-y-3">
                        {facts.map((fact) => (
                            <li key={fact.key}>
                                <span className="text-foreground">{fact.question}</span>
                                {!fact.required && <span className="ml-2 text-xs">(optional)</span>}
                                {fact.used_for && <span className="block text-xs">{fact.used_for}</span>}
                                {fact.example && <span className="block text-xs italic">e.g. {fact.example}</span>}
                            </li>
                        ))}
                    </ul>
                </Section>
            )}

            {connectors.length > 0 && (
                <Section title="Apps it can use">
                    <ul className="space-y-2">
                        {connectors.map((app) => (
                            <li key={app.app}>
                                <span className="text-foreground">{app.label}</span>
                                {app.used_for && <span className="block text-xs">{app.used_for}</span>}
                            </li>
                        ))}
                    </ul>
                </Section>
            )}

            {pack.guardrails.length > 0 && (
                <Section title="What it will never do">
                    <ul className="list-disc space-y-2 pl-5">
                        {pack.guardrails.map((rule) => (
                            <li key={rule}>{rule}</li>
                        ))}
                    </ul>
                </Section>
            )}

            {pack.compliance_notes.length > 0 && (
                <Section title="Compliance">
                    <ul className="list-disc space-y-2 pl-5">
                        {pack.compliance_notes.map((note) => (
                            <li key={note}>{note}</li>
                        ))}
                    </ul>
                </Section>
            )}
        </main>
    );
}
