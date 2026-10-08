"use client";

/**
 * What a person follows for trading summaries, inside Research's detail.
 * Their own list, never the workspace's. A summary is information only:
 * the notice from the server is shown beside the button, every time.
 */

import { Loader2, X } from "lucide-react";
import { useEffect, useState } from "react";

import {
    askTradingSummaryApiV1HelpersResearchTradingSummaryPost,
    myInterestsApiV1HelpersResearchInterestsGet,
    saveMyInterestsApiV1HelpersResearchInterestsPut,
} from "@/client/sdk.gen";
import type { Interest } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { detailFromResult } from "@/lib/apiError";

type Kind = "ticker" | "sector" | "topic";

export function TradingInterests({ threadId, available }: { threadId: string | null; available: boolean }) {
    const [items, setItems] = useState<Interest[] | null>(null);
    const [saved, setSaved] = useState<Interest[]>([]);
    const [revision, setRevision] = useState(0);
    const [notice, setNotice] = useState("");
    const [label, setLabel] = useState("");
    const [kind, setKind] = useState<Kind>("ticker");
    const [status, setStatus] = useState<string | null>(null);
    const [busy, setBusy] = useState(false);
    const [failed, setFailed] = useState<string | null>(null);

    useEffect(() => {
        void (async () => {
            const response = await myInterestsApiV1HelpersResearchInterestsGet();
            if (response.error || !response.data) {
                setFailed(detailFromResult(response, "Could not load what you follow."));
                return;
            }
            setItems(response.data.interests);
            setSaved(response.data.interests);
            setRevision(response.data.revision);
            setNotice(response.data.notice);
        })();
    }, []);

    if (failed) return <p className="text-sm text-destructive">{failed}</p>;
    if (items === null) return <p className="text-sm text-muted-foreground">Loading what you follow…</p>;

    const dirty = JSON.stringify(items) !== JSON.stringify(saved);

    const save = async () => {
        setBusy(true);
        setStatus(null);
        const response = await saveMyInterestsApiV1HelpersResearchInterestsPut({ body: { interests: items, revision } });
        setBusy(false);
        if (response.error) {
            const stored = (response.error as { detail?: { stored?: { interests: Interest[]; revision: number } } })
                ?.detail?.stored;
            if (stored) {
                // Saved elsewhere since: keep the draft, show what is stored.
                setRevision(stored.revision);
                setSaved(stored.interests);
                setStatus("This changed somewhere else. Your list is kept here; save again to replace it.");
                return;
            }
            setStatus(detailFromResult(response, "Your change was not saved. Try again."));
            return;
        }
        setSaved(response.data!.interests);
        setItems(response.data!.interests);
        setRevision(response.data!.revision);
        setStatus("Saved.");
    };

    const summarise = async () => {
        setBusy(true);
        setStatus(null);
        const response = await askTradingSummaryApiV1HelpersResearchTradingSummaryPost({ body: { thread_id: threadId } });
        setBusy(false);
        setStatus(
            response.error
                ? detailFromResult(response, "Could not ask for the summary.")
                : "Asked. The summary will appear in this conversation.",
        );
    };

    return (
        <section className="space-y-2" aria-label="Trading summary">
            <p className="text-sm font-medium">Trading summary: what you follow</p>
            <ul className="flex flex-wrap gap-2" aria-label="What you follow">
                {items.length === 0 && <li className="text-sm text-muted-foreground">Nothing yet.</li>}
                {items.map((item) => (
                    <li key={`${item.kind}:${item.label}`} className="flex items-center gap-1 rounded-md border border-border px-2 py-1 text-xs">
                        {item.label}
                        <span className="text-muted-foreground">({item.kind})</span>
                        <button
                            type="button"
                            className="flex h-6 w-6 items-center justify-center max-md:h-11 max-md:w-11"
                            aria-label={`Stop following ${item.label}`}
                            onClick={() => setItems(items.filter((i) => i !== item))}
                        >
                            <X aria-hidden className="h-3 w-3" />
                        </button>
                    </li>
                ))}
            </ul>
            <form
                className="flex flex-wrap gap-2"
                onSubmit={(event) => {
                    event.preventDefault();
                    const text = label.trim();
                    if (!text) return;
                    setItems([...items, { label: kind === "ticker" ? text.toUpperCase() : text, kind }]);
                    setLabel("");
                }}
            >
                <label className="sr-only" htmlFor="interest-label">
                    Add something to follow
                </label>
                <input
                    id="interest-label"
                    value={label}
                    onChange={(e) => setLabel(e.target.value)}
                    placeholder="TCS, banking, gold…"
                    className="min-h-11 min-w-0 flex-1 rounded-md border border-border bg-background px-3 text-base md:text-sm"
                />
                <label className="sr-only" htmlFor="interest-kind">
                    Kind
                </label>
                <select
                    id="interest-kind"
                    value={kind}
                    onChange={(e) => setKind(e.target.value as Kind)}
                    className="min-h-11 rounded-md border border-border bg-background px-2 text-base md:text-sm"
                >
                    <option value="ticker">Ticker</option>
                    <option value="sector">Sector</option>
                    <option value="topic">Topic</option>
                </select>
                <Button type="submit" variant="outline" className="min-h-11">
                    Add
                </Button>
            </form>
            <div className="flex flex-wrap gap-2">
                <Button type="button" variant="outline" className="min-h-11" disabled={!dirty || busy} onClick={() => void save()}>
                    Save list
                </Button>
                <Button
                    type="button"
                    className="min-h-11"
                    disabled={busy || dirty || saved.length === 0 || !available}
                    onClick={() => void summarise()}
                >
                    {busy && <Loader2 aria-hidden className="h-4 w-4 animate-spin" />}
                    Summarise now
                </Button>
            </div>
            {notice && <p className="text-xs text-muted-foreground">{notice}</p>}
            {status && (
                <p className="text-sm" role="status">
                    {status}
                </p>
            )}
        </section>
    );
}
