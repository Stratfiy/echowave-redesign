"use client";

/**
 * "Save" under one of Decibyl's replies (screen 15: a saved result). Keeps a
 * copy in the person's own saved items, with the way back to this
 * conversation. Saved means the server said so; a failure says it was not.
 */

import { Bookmark, BookmarkCheck, Loader2 } from "lucide-react";
import Link from "next/link";
import { useState } from "react";

import { saveItemApiV1MeSavedPost } from "@/client/sdk.gen";
import type { TimelineEvent } from "@/client/types.gen";

function titleOf(event: TimelineEvent): string {
    const body = String(((event.payload ?? {}) as { body?: unknown }).body ?? event.summary ?? "").trim();
    const line = body.split("\n")[0] ?? "";
    return (line.length > 80 ? `${line.slice(0, 77)}…` : line) || "A reply from Decibyl";
}

export function SaveReplyButton({ event, threadId }: { event: TimelineEvent; threadId?: string | null }) {
    const [state, setState] = useState<"idle" | "saving" | "saved" | "failed">("idle");
    const [itemId, setItemId] = useState<number | null>(null);
    if (state === "saved" && itemId) {
        return (
            <p className="mt-1 inline-flex items-center gap-1 text-xs text-muted-foreground" role="status">
                <BookmarkCheck aria-hidden className="h-3.5 w-3.5" /> Saved.{" "}
                <Link href={`/settings/saved?item=${itemId}`} className="underline underline-offset-2">
                    Open
                </Link>
            </p>
        );
    }
    return (
        <span className="mt-1 inline-flex items-center gap-2">
            <button
                type="button"
                disabled={state === "saving"}
                onClick={async () => {
                    setState("saving");
                    try {
                        const result = await saveItemApiV1MeSavedPost({
                            body: { title: titleOf(event), kind: "reply", source_event_id: event.id, thread_id: threadId ?? null },
                        });
                        if (result.error || !result.data) {
                            setState("failed");
                            return;
                        }
                        setItemId(result.data.id);
                        setState("saved");
                    } catch {
                        setState("failed");
                    }
                }}
                className="motion-m1 inline-flex min-h-11 items-center gap-1 rounded-md px-2 text-xs text-muted-foreground hover:bg-accent md:min-h-7"
                aria-label="Save this reply"
            >
                {state === "saving" ? <Loader2 aria-hidden className="motion-continuous h-3.5 w-3.5 animate-spin" /> : <Bookmark aria-hidden className="h-3.5 w-3.5" />}
                Save
            </button>
            {state === "failed" && (
                <span role="alert" className="text-xs text-destructive">
                    Not saved. Try again.
                </span>
            )}
        </span>
    );
}

export default SaveReplyButton;
