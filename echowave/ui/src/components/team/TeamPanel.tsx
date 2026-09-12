"use client";

/**
 * The team: every agent in the account, listed like people rather than like
 * rows in a configuration table.
 *
 * The whole point is the second line. A row called "Front Desk" with a pencil
 * icon beside it tells an owner nothing; "9 calls, 6 answered, 4 bookings"
 * tells them the thing they hired it for is happening. That sentence is built
 * on the server from what the agent actually did — the run table and the
 * record of every action it took in outside software — so it cannot drift from
 * reality the way a status field somebody has to remember to update does.
 *
 * Agents needing attention sort first. An owner should never have to scroll to
 * find the one that stopped filing bookings.
 */

import { formatDistanceToNow } from "date-fns";
import { ArrowRight, Plus } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { teamStatusApiV1TeamStatusGet } from "@/client/sdk.gen";
import type { TeamMember } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { cn } from "@/lib/utils";

/** The dot beside the name. Four states, because "paused" and "quiet" mean
 *  different things and only one of them is worth looking into. */
const TONE_DOT: Record<string, string> = {
    attention: "bg-destructive",
    working: "bg-emerald-500",
    idle: "bg-muted-foreground/40",
    paused: "bg-amber-500",
};

const TONE_TEXT: Record<string, string> = {
    attention: "text-destructive",
    working: "text-muted-foreground",
    idle: "text-muted-foreground",
    paused: "text-amber-600",
};

/** Initials for the avatar. Two letters at most: "Front Desk" -> FD. */
function initials(name: string): string {
    const words = name.trim().split(/\s+/).filter(Boolean);
    if (words.length === 0) return "?";
    if (words.length === 1) return words[0].slice(0, 2).toUpperCase();
    return (words[0][0] + words[1][0]).toUpperCase();
}

function ago(at: string | null | undefined): string | null {
    if (!at) return null;
    const date = new Date(at);
    if (Number.isNaN(date.getTime())) return null;
    return formatDistanceToNow(date, { addSuffix: true });
}

function MemberRow({ member }: { member: TeamMember }) {
    const when = ago(member.at);
    return (
        <Link
            href={`/workflow/${member.workflow_id}`}
            className="flex items-center gap-3 rounded-lg px-2 py-2.5 transition-colors hover:bg-muted/60"
        >
            <div className="flex h-9 w-9 shrink-0 items-center justify-center rounded-full bg-muted text-xs font-semibold text-muted-foreground">
                {initials(member.name)}
            </div>
            <div className="min-w-0 flex-1">
                <div className="flex items-center gap-2">
                    <span className={cn("h-1.5 w-1.5 shrink-0 rounded-full", TONE_DOT[member.tone] ?? TONE_DOT.idle)} />
                    <span className="truncate text-sm font-medium">{member.name}</span>
                </div>
                <p className={cn("truncate pl-3.5 text-xs", TONE_TEXT[member.tone] ?? "text-muted-foreground")}>
                    {member.status}
                    {when ? <span className="text-muted-foreground"> · {when}</span> : null}
                </p>
            </div>
            <ArrowRight className="h-4 w-4 shrink-0 text-muted-foreground/50" />
        </Link>
    );
}

export function TeamPanel() {
    const [members, setMembers] = useState<TeamMember[] | null>(null);
    const [failed, setFailed] = useState(false);

    useEffect(() => {
        let cancelled = false;
        (async () => {
            try {
                const response = await teamStatusApiV1TeamStatusGet({ query: { hours: 24 } });
                if (!cancelled) setMembers(response.data?.members ?? []);
            } catch {
                // A failure here must not take the home screen down with it.
                // The panel says so and the rest of the page still renders.
                if (!cancelled) setFailed(true);
            }
        })();
        return () => {
            cancelled = true;
        };
    }, []);

    if (failed) return null;
    if (members === null) {
        return (
            <Card>
                <CardHeader className="pb-3">
                    <CardTitle className="text-base">Your team</CardTitle>
                </CardHeader>
                <CardContent className="space-y-3 pb-4">
                    {[0, 1, 2].map((i) => (
                        <div key={i} className="flex items-center gap-3">
                            <div className="h-9 w-9 animate-pulse rounded-full bg-muted" />
                            <div className="flex-1 space-y-1.5">
                                <div className="h-3 w-28 animate-pulse rounded bg-muted" />
                                <div className="h-2.5 w-44 animate-pulse rounded bg-muted" />
                            </div>
                        </div>
                    ))}
                </CardContent>
            </Card>
        );
    }

    // No agents at all is the empty state the rest of this screen already
    // handles — a second "hire your first agent" card under it would be two
    // doors into the same room.
    if (members.length === 0) return null;

    return (
        <Card>
            <CardHeader className="flex flex-row items-start justify-between gap-2 space-y-0 pb-2">
                <div>
                    <CardTitle className="text-base">Your team</CardTitle>
                    <CardDescription>What each agent has done in the last 24 hours.</CardDescription>
                </div>
                <Button asChild size="sm" variant="outline">
                    <Link href="/workflow/create">
                        <Plus className="mr-1 h-3.5 w-3.5" />
                        Hire
                    </Link>
                </Button>
            </CardHeader>
            <CardContent className="pb-3 pt-1">
                <div className="-mx-2 divide-y">
                    {members.map((member) => (
                        <MemberRow key={member.workflow_id} member={member} />
                    ))}
                </div>
            </CardContent>
        </Card>
    );
}
