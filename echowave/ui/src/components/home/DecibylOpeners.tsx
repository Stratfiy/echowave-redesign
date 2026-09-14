'use client';

/**
 * Home is a chat with Decibyl, the way Slack's home is a chat with Slackbot:
 * "Hi, I'm Decibyl", two questions worth asking on arrival, and the composer
 * that builds an agent from a sentence underneath.
 *
 * The two openers are answered from the account's own numbers, not by a
 * model: what happened this week and what needs attention are facts the API
 * already returns for the greeting, and a sentence built from facts is
 * right every time. The answer appears as a reply under the question, so
 * the screen reads as the conversation it is.
 */

import { ArrowRight, Bell, Sparkles } from 'lucide-react';
import Link from 'next/link';
import { useState } from 'react';

import { teamHomeApiV1TeamHomeGet } from '@/client/sdk.gen';
import type { Headline, Suggestion, TeamMember } from '@/client/types.gen';
import { detailFromResult } from '@/lib/apiError';

export type Exchange = { question: string; lines: string[]; links: { text: string; href: string }[] };

function plural(n: number, one: string, many: string): string {
    return `${n} ${n === 1 ? one : many}`;
}

/** "What happened this week?", in sentences. */
export function whatHappened(headline: Headline, members: TeamMember[], hours: number): string[] {
    const span = hours >= 168 ? 'this week' : 'today';
    if (headline.agents === 0) return ['No bots yet. Describe your business below and I will build the first one.'];
    const lines: string[] = [];
    if (headline.calls === 0) {
        lines.push(`No calls ${span}. ${plural(headline.live, 'bot is', 'bots are')} live and waiting.`);
    } else {
        lines.push(
            `${plural(headline.calls, 'call', 'calls')} ${span}, ${headline.answered} answered, ` +
                `${plural(headline.outcomes, 'outcome', 'outcomes')} filed.`,
        );
    }
    const busy = [...members].filter((m) => m.calls > 0).sort((a, b) => b.calls - a.calls).slice(0, 5);
    for (const m of busy) {
        lines.push(`${m.name}: ${plural(m.calls, 'call', 'calls')}, ${m.answered} answered, ${m.outcomes} filed.`);
    }
    return lines;
}

/** "What needs my attention today?", in sentences, with the doors. */
export function needsAttention(
    headline: Headline,
    members: TeamMember[],
    suggestions: Suggestion[],
): { lines: string[]; links: { text: string; href: string }[] } {
    const lines: string[] = [];
    const failing = members.filter((m) => m.failures > 0);
    for (const m of failing) lines.push(`${m.name} failed ${plural(m.failures, 'time', 'times')} today.`);
    const quiet = members.filter((m) => m.calls > 0 && m.outcomes === 0);
    for (const m of quiet) lines.push(`${m.name} took ${plural(m.calls, 'call', 'calls')} and filed nothing.`);
    const links = suggestions
        .filter((s) => s.action === 'link' && s.href)
        .map((s) => ({ text: s.text, href: s.href as string }));
    if (lines.length === 0 && links.length === 0) {
        lines.push(headline.needs_attention > 0 ? `${plural(headline.needs_attention, 'bot needs', 'bots need')} a look.` : 'Nothing needs you right now.');
    }
    return { lines, links };
}

export function DecibylOpeners({
    headline,
    members,
    suggestions,
}: {
    headline: Headline | null;
    members: TeamMember[];
    suggestions: Suggestion[];
}) {
    const [exchange, setExchange] = useState<Exchange | null>(null);
    const [busy, setBusy] = useState<string | null>(null);

    const askWeek = async () => {
        setBusy('week');
        const response = await teamHomeApiV1TeamHomeGet({ query: { hours: 168 } });
        setBusy(null);
        if (response.error || !response.data) {
            setExchange({ question: 'What happened this week?', lines: [detailFromResult(response, 'Could not read the week')], links: [] });
            return;
        }
        setExchange({
            question: 'What happened this week?',
            lines: whatHappened(response.data.headline, response.data.members ?? [], 168),
            links: [],
        });
    };

    const askAttention = () => {
        if (!headline) return;
        const answer = needsAttention(headline, members, suggestions);
        setExchange({ question: 'What needs my attention today?', ...answer });
    };

    return (
        <div className="space-y-3">
            <div className="grid gap-3 sm:grid-cols-2">
                <button
                    type="button"
                    onClick={() => void askWeek()}
                    disabled={busy !== null}
                    className="flex items-center justify-between gap-3 rounded-xl border border-border bg-card px-4 py-3 text-left text-sm font-medium shadow-sm hover:bg-accent/40 disabled:opacity-60"
                >
                    <span className="flex items-center gap-2">
                        <Sparkles className="h-4 w-4 text-[var(--accent-brand)]" aria-hidden />
                        What happened this week?
                    </span>
                    <ArrowRight className="h-4 w-4 text-muted-foreground" aria-hidden />
                </button>
                <button
                    type="button"
                    onClick={askAttention}
                    disabled={!headline}
                    className="flex items-center justify-between gap-3 rounded-xl border border-border bg-card px-4 py-3 text-left text-sm font-medium shadow-sm hover:bg-accent/40 disabled:opacity-60"
                >
                    <span className="flex items-center gap-2">
                        <Bell className="h-4 w-4 text-[var(--accent-brand)]" aria-hidden />
                        What needs my attention today?
                    </span>
                    <ArrowRight className="h-4 w-4 text-muted-foreground" aria-hidden />
                </button>
            </div>
            {exchange && (
                <div className="space-y-2" aria-label="Decibyl's answer">
                    <div className="flex justify-end">
                        <p className="max-w-[85%] rounded-lg bg-primary px-3 py-2 text-sm text-primary-foreground">{exchange.question}</p>
                    </div>
                    <div className="flex justify-start">
                        <div className="max-w-[85%] space-y-1 rounded-lg border border-border bg-muted/40 px-3 py-2 text-sm">
                            {exchange.lines.map((line, i) => (
                                <p key={i}>{line}</p>
                            ))}
                            {exchange.links.map((link) => (
                                <p key={link.href}>
                                    <Link href={link.href} className="underline underline-offset-2">
                                        {link.text}
                                    </Link>
                                </p>
                            ))}
                        </div>
                    </div>
                </div>
            )}
        </div>
    );
}

export default DecibylOpeners;
