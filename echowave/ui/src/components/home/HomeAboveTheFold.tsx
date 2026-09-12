"use client";

/**
 * The half of the home screen you see before scrolling.
 *
 * A greeting in sentences, a composer, four chips built from what is actually
 * true of this account, and the team. The charts live below the fold and mount
 * only when somebody scrolls to them.
 *
 * The order is the argument. What happened, in words, above; how it is
 * trending, in charts, below. An owner opening this at 9am wants to know what
 * the night shift did, not what the answer rate was over ninety days.
 *
 * One request feeds all of it. The greeting, the chips and the team are three
 * readings of the same rows, so fetching them separately would be three round
 * trips to say one thing — and would let the greeting and the list disagree.
 */

import { AlertTriangle, ArrowRight } from "lucide-react";
import Link from "next/link";
import { useEffect, useMemo, useState } from "react";

import { teamHomeApiV1TeamHomeGet } from "@/client/sdk.gen";
import type { Headline, Suggestion, TeamMember } from "@/client/types.gen";
import { AgentBuilderPanel, type Prefill } from "@/components/agent-builder/AgentBuilderPanel";
import { TeamPanel } from "@/components/team/TeamPanel";
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

function Chip({ chip, onPrompt }: { chip: Suggestion; onPrompt: (text: string) => void }) {
    const className =
        "inline-flex items-center gap-1.5 rounded-full border border-border bg-muted/30 px-3 py-1.5 text-xs text-muted-foreground transition-colors hover:bg-muted hover:text-foreground focus-visible:outline-none focus-visible:ring-1 focus-visible:ring-ring";

    // A link chip opens a screen; a prompt chip fills the composer and waits
    // for the person to press send. Nothing here runs anything — a chip that
    // silently started calling customers is how an account is lost.
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
        <button type="button" className={className} onClick={() => onPrompt(chip.prompt ?? chip.text)}>
            {chip.text}
        </button>
    );
}

export function HomeAboveTheFold({ firstName }: { firstName?: string }) {
    const { user, loading: authLoading } = useAuth();
    const [headline, setHeadline] = useState<Headline | null>(null);
    const [suggestions, setSuggestions] = useState<Suggestion[]>([]);
    const [members, setMembers] = useState<TeamMember[] | null>(null);
    const [prefill, setPrefill] = useState<Prefill | undefined>(undefined);

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
                setHeadline(response.data.headline);
                setSuggestions(response.data.suggestions);
                setMembers(response.data.members);
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
            <div>
                <h2 className="text-xl font-semibold tracking-tight">
                    {greeting}
                    {firstName ? `, ${firstName}` : ""}.
                </h2>
                <p className="mt-0.5 text-sm text-muted-foreground">
                    {headline ? summarise(headline) : " "}
                </p>
            </div>

            <AgentBuilderPanel prefill={prefill} showSuggestions={suggestions.length === 0} />

            {suggestions.length > 0 ? (
                <div className="-mt-4 flex flex-wrap gap-2">
                    {suggestions.map((chip) => (
                        <Chip
                            key={`${chip.kind}-${chip.text}`}
                            chip={chip}
                            onPrompt={(text) =>
                                setPrefill((previous) => ({
                                    text,
                                    nonce: (previous?.nonce ?? 0) + 1,
                                }))
                            }
                        />
                    ))}
                </div>
            ) : null}

            {members && members.length > 0 ? <TeamPanel members={members} /> : null}
        </div>
    );
}
