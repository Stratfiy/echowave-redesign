"use client";

/**
 * Possible duplicates, both sides shown, merged only when the person says
 * so. "Keep both" is remembered; the pair is not suggested again.
 */

import { Loader2 } from "lucide-react";
import { useState } from "react";

import { decideMergeApiV1PeopleMergesMergeIdPost } from "@/client/sdk.gen";
import type { MergeSuggestion, PersonSummary } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { detailFromResult } from "@/lib/apiError";
import { sourceLabel } from "@/lib/people/format";

function Side({ person }: { person: PersonSummary }) {
    return (
        <div className="min-w-0 flex-1 rounded-[6px] bg-muted/40 p-3 text-sm">
            <p className="break-words font-medium">{person.name}</p>
            {person.company && <p className="text-muted-foreground">{person.company}</p>}
            {[...(person.phones ?? []), ...(person.emails ?? [])].map((h) => (
                <p key={h} className="break-all text-muted-foreground">
                    {h}
                </p>
            ))}
            <p className="mt-1 text-xs text-muted-foreground">{(person.sources ?? []).map(sourceLabel).join(" · ")}</p>
        </div>
    );
}

export function MergeReview({ merges, onDecided }: { merges: MergeSuggestion[]; onDecided: () => void }) {
    const [busy, setBusy] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);

    const decide = async (id: string, action: "merge" | "keep_both") => {
        setBusy(id);
        setError(null);
        const result = await decideMergeApiV1PeopleMergesMergeIdPost({
            path: { merge_id: id },
            body: { action },
        });
        setBusy(null);
        if (result.error) {
            setError(detailFromResult(result, "Could not save that"));
            return;
        }
        onDecided();
    };

    return (
        <section
            aria-label="Possible duplicates"
            className="rounded-[8px] border border-amber-300/60 bg-amber-50/40 p-4 dark:bg-amber-950/20"
            data-testid="people-merges"
        >
            <h2 className="text-sm font-semibold">{merges.length === 1 ? "1 possible duplicate" : `${merges.length} possible duplicates`}</h2>
            <p className="text-sm text-muted-foreground">Nothing is merged until you say so.</p>
            {error && (
                <p role="alert" className="mt-2 text-sm text-destructive">
                    {error}
                </p>
            )}
            <ul className="mt-3 flex flex-col gap-4">
                {merges.map((m) => (
                    <li key={m.id}>
                        <p className="mb-2 text-xs text-muted-foreground">
                            Same {m.reason === "phone" ? "number" : "address"}: <span className="break-all">{m.value}</span>
                        </p>
                        <div className="flex flex-col gap-2 sm:flex-row">
                            <Side person={m.keep} />
                            <Side person={m.other} />
                        </div>
                        <div className="mt-2 flex flex-wrap gap-2">
                            <Button size="sm" className="min-h-11 md:min-h-9" disabled={busy === m.id} onClick={() => void decide(m.id, "merge")}>
                                {busy === m.id && <Loader2 aria-hidden className="animate-spin" />}
                                Merge into one
                            </Button>
                            <Button
                                size="sm"
                                variant="outline"
                                className="min-h-11 md:min-h-9"
                                disabled={busy === m.id}
                                onClick={() => void decide(m.id, "keep_both")}
                            >
                                Keep both
                            </Button>
                        </div>
                    </li>
                ))}
            </ul>
        </section>
    );
}

export default MergeReview;
