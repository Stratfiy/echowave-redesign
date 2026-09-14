"use client";

/**
 * Decibyl's About, the way every bot has one.
 *
 * Who it is, what it reads, what it can propose, and what it remembers --
 * the last with a way to forget. The same memory list a bot's About shows,
 * scoped to the organisation because Decibyl speaks for the business, not
 * for one bot. Nothing here is a setting; it is the profile of the
 * assistant on Home, so somebody can see what it draws on before trusting
 * an answer, and take a wrong fact out of its mouth in one click.
 */

import { Brain, Eye, Zap } from "lucide-react";

import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { MemoryList } from "@/components/memory/MemoryList";

const READS = [
    "The team's numbers: calls, answers, outcomes, who needs attention, today or this week.",
    "What the business has confirmed about itself, below.",
    "What every bot did lately: calls ended, outcomes filed, hand-offs, failures.",
    "Missed calls nobody has returned.",
    "Company knowledge, the passages that match the question.",
];

const PROPOSES = [
    "Turn a bot on or off",
    "Call a missed caller back",
    "Forget a fact from memory",
];

export default function DecibylAboutPage() {
    return (
        <>
            <PageHeader
                title="Decibyl"
                description="Your team's assistant. Ask what happened, or build a new bot."
                tabs={[
                    { href: "/overview", label: "Messages" },
                    { href: "/review", label: "History", prefix: true },
                    { href: "/overview/about", label: "About" },
                ]}
            />
            <PageBody className="max-w-2xl space-y-8">
                <div className="flex items-center gap-3">
                    <span
                        aria-hidden
                        className="flex h-12 w-12 shrink-0 items-center justify-center rounded-lg bg-rail text-lg font-semibold text-rail-foreground"
                    >
                        d
                    </span>
                    <div>
                        <p className="text-base font-semibold">Decibyl</p>
                        <p className="text-xs text-muted-foreground">
                            Answers from the workspace&apos;s own readings. Never invents a figure.
                        </p>
                    </div>
                </div>

                <section>
                    <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                        <Eye className="h-3.5 w-3.5" aria-hidden />
                        What it reads
                    </h3>
                    <ul className="list-disc space-y-1 pl-5 text-sm">
                        {READS.map((line) => (
                            <li key={line}>{line}</li>
                        ))}
                    </ul>
                </section>

                <section>
                    <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                        <Zap className="h-3.5 w-3.5" aria-hidden />
                        What it can propose
                    </h3>
                    <ul className="flex flex-wrap gap-2" aria-label="What it can propose">
                        {PROPOSES.map((line) => (
                            <li
                                key={line}
                                className="rounded-full border border-border bg-muted/30 px-3 py-1 text-xs"
                            >
                                {line}
                            </li>
                        ))}
                    </ul>
                    <p className="mt-2 text-sm text-muted-foreground">
                        Nothing runs until you confirm on the card in the thread, and you have
                        ten seconds to undo. A switch can be put back afterwards; a call cannot.
                    </p>
                </section>

                <section>
                    <h3 className="mb-2 flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                        <Brain className="h-3.5 w-3.5" aria-hidden />
                        Memory
                    </h3>
                    <p className="mb-2 text-sm text-muted-foreground">
                        What the business has confirmed. Every bot reads it. Forget a line here,
                        or say &quot;forget our Saturday hours&quot; in the thread.
                    </p>
                    <MemoryList />
                </section>
            </PageBody>
        </>
    );
}
