"use client";

/**
 * The half of the home screen you see before scrolling.
 *
 * Decibyl says hello, answers the two questions an owner opens the app with,
 * and offers the chips built from what is actually true of this account.
 * Nothing else: the builder has its own door (Hire, the marketplace) and the
 * bots have the panel and the rail. A home that also carried a builder box
 * and a team table was three screens stacked, and the one that mattered was
 * the one people scrolled past.
 *
 * One request feeds all of it. The greeting, the answers and the chips are
 * readings of the same rows, so fetching them separately would be three
 * round trips to say one thing — and would let them disagree.
 */

import { AlertTriangle, ArrowRight } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { teamHomeApiV1TeamHomeGet } from "@/client/sdk.gen";
import type { Headline, Suggestion, TeamMember } from "@/client/types.gen";
import { DecibylOpeners } from "@/components/home/DecibylOpeners";
import { useAuth } from "@/lib/auth";

/** Built from the reader's own clock. The server's is in a data centre, and
 *  half the accounts would be wished good morning at nine in the evening. */
function partOfDay(now: Date): string {
    const hour = now.getHours();
    if (hour < 12) return "Good morning";
    if (hour < 17) return "Good afternoon";
    return "Good evening";
}

/** What the team did, as a sentence rather than a row of tiles.
 *
 *  Silence is stated, never left blank: "Nothing has come in yet today" is
 *  information, an empty line is a screen somebody stops trusting. */
function summarise(headline: Headline): string {
    if (headline.agents === 0) return "Let's put your first agent to work.";

    const parts: string[] = [];
    if (headline.calls > 0) {
        parts.push(`${headline.calls} ${headline.calls === 1 ? "call" : "calls"} today`);
        if (headline.answered > 0) parts.push(`${headline.answered} answered`);
        if (headline.outcomes > 0) parts.push(`${headline.outcomes} finished`);
    }

    if (parts.length === 0) {
        return headline.live === 0
            ? "No agent is taking calls right now."
            : "Nothing has come in yet today.";
    }

    const sentence = `${parts.join(", ")}.`;
    if (headline.needs_attention > 0) {
        return `${sentence} ${headline.needs_attention} ${
            headline.needs_attention === 1 ? "agent needs" : "agents need"
        } you.`;
    }
    return sentence;
}

function Chip({ chip }: { chip: Suggestion }) {
    const className =
        "inline-flex items-center gap-1.5 rounded-full border border-border bg-muted/30 px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring";

    // A link chip opens the screen that fixes the thing. A prompt chip used
    // to fill the builder box that sat here; the builder now lives behind
    // Hire, so the chip opens the shelf instead. Nothing here runs anything
    // — a chip that silently started calling customers is how an account is
    // lost.
    if (chip.action === "link" && chip.href) {
        return (
            <Link href={chip.href} className={className}>
                <AlertTriangle className="h-3 w-3" />
                {chip.text}
                <ArrowRight className="h-3 w-3" />
            </Link>
        );
    }
    return (
        <Link href="/marketplace" className={className}>
            {chip.text}
            <ArrowRight className="h-3 w-3" />
        </Link>
    );
}

export function HomeAboveTheFold({ firstName }: { firstName?: string }) {
    const { user, loading: authLoading } = useAuth();
    const [headline, setHeadline] = useState<Headline | null>(null);
    const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
    const [members, setMembers] = useState<TeamMember[] | null>(null);

    // Computed once per mount. Reading the clock during render would make the
    // greeting change under a re-render at a boundary hour.
    const greeting = useMemo(() => partOfDay(new Date()), []);

    useEffect(() => {
        // The auth interceptor only attaches the token once auth has settled;
        // fetching earlier sends an unauthenticated request that fails quietly.
        if (authLoading || !user) return;

        let cancelled = false;
        (async () => {
            try {
                const response = await teamHomeApiV1TeamHomeGet({ query: { hours: 24 } });
                if (cancelled || response.error || !response.data) return;
                // Defaulted rather than trusted. The shapes come from our own
                // schema, but a field that arrives missing must leave the
                // screen a sentence short — not throw the whole home page away.
                setHeadline(response.data.headline ?? null);
                setSuggestions(response.data.suggestions ?? []);
                setMembers(response.data.members ?? []);
            } catch {
                // The composer below still works. A greeting that failed to
                // load is a missing sentence, not a broken screen.
            }
        })();
        return () => {
            cancelled = true;
        };
    }, [authLoading, user]);

    return (
        <div className="space-y-5">
            {/* Home is a conversation with Decibyl, the way a Slack workspace
                opens on Slackbot: a mark, an introduction, and two questions
                it already knows the answer to. The builder composer below is
                the reply box. */}
            <div className="flex items-start gap-3">
                <div
                    aria-hidden="true"
                    className="flex h-10 w-10 shrink-0 items-center justify-center rounded-lg bg-rail text-lg font-semibold text-rail-foreground"
                >
                    d
                </div>
                <div className="min-w-0">
                    <h2 className="text-xl font-semibold tracking-tight">Hi, I&apos;m Decibyl.</h2>
                    <p className="mt-0.5 text-sm text-muted-foreground">
                        {greeting}
                        {firstName ? `, ${firstName}` : ""}. {headline ? summarise(headline) : ""}
                    </p>
                </div>
            </div>

            {headline ? (
                <DecibylOpeners headline={headline} members={members ?? []} suggestions={suggestions} />
            ) : null}

            {suggestions.length > 0 ? (
                <div className="flex flex-wrap gap-2">
                    {suggestions.map((chip) => (
                        <Chip key={`${chip.kind}-${chip.text}`} chip={chip} />
                    ))}
                </div>
            ) : null}
        </div>
    );
}
