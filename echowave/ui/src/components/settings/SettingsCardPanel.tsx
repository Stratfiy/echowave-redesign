"use client";

/**
 * A card a Settings screen raised -- forget a memory, delete a saved item,
 * delete your data -- shown where it was asked for (never another screen),
 * on the shell's ActionPreview. The same controls card underneath as in Chat:
 * Confirm approves exactly the version shown, it runs once after a short
 * undo window, and what happened is the card's own state, read back from the
 * server -- never a toast standing in for it.
 */

import { CheckCircle2, CircleAlert, Loader2, Undo2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import { settingsCardApiV1MeSettingsCardsEventIdGet, settleSettingsCardApiV1MeSettingsCardsEventIdSettlePost } from "@/client/sdk.gen";
import type { SettingsCard } from "@/client/types.gen";
import { ActionPreview, type ApprovalStatus } from "@/components/shell/ActionPreview";
import { Button } from "@/components/ui/button";
import { detailFromError } from "@/lib/apiError";

const SETTLED = new Set(["done", "failed", "declined", "cancelled", "undone", "outcome_unknown"]);

function status(card: SettingsCard, busy: boolean): ApprovalStatus {
    if (busy) return "committing";
    if (card.state === "declined" || card.state === "cancelled") return "cancelled";
    if (card.state === "proposed") return "pending";
    return "approved";
}

export function SettingsCardPanel({
    card: initial,
    onSettled,
    pollMs = 1500,
}: {
    card: SettingsCard;
    /** Told once the card reaches a final state, so the list can reload. */
    onSettled?: (card: SettingsCard) => void;
    pollMs?: number;
}) {
    const [card, setCard] = useState<SettingsCard>(initial);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const told = useRef<string | null>(null);

    const refresh = useCallback(async () => {
        const result = await settingsCardApiV1MeSettingsCardsEventIdGet({
            path: { event_id: card.event_id },
            query: { organization_id: card.organization_id },
        });
        if (!result.error && result.data) setCard(result.data);
    }, [card.event_id, card.organization_id]);

    // While armed or running, follow the server until it settles.
    useEffect(() => {
        if (card.state !== "armed" && card.state !== "running") return;
        const timer = setTimeout(() => void refresh(), pollMs);
        return () => clearTimeout(timer);
    }, [card.state, refresh, pollMs]);

    useEffect(() => {
        if (SETTLED.has(card.state) && told.current !== card.state) {
            told.current = card.state;
            onSettled?.(card);
        }
    }, [card, onSettled]);

    const settle = async (verb: "confirm" | "decline" | "undo") => {
        setBusy(true);
        setError(null);
        try {
            const result = await settleSettingsCardApiV1MeSettingsCardsEventIdSettlePost({
                path: { event_id: card.event_id },
                body: { organization_id: card.organization_id, verb, version: verb === "confirm" ? card.version : null },
            });
            if (result.error || !result.data) {
                setError(detailFromError(result.error, "That did not go through. Nothing was changed."));
                await refresh();
                return;
            }
            setCard(result.data);
        } catch {
            setError("Could not reach Decibyl. Nothing was changed.");
        } finally {
            setBusy(false);
        }
    };

    return (
        <div className="flex flex-col gap-2" data-testid="settings-card" data-state={card.state}>
            <ActionPreview
                preview={{
                    id: String(card.event_id),
                    version: 1,
                    action: card.label,
                    content: card.effect,
                    consequence: card.reversible ? "You can put it back." : "This cannot be undone.",
                }}
                status={status(card, busy)}
                onApprove={() => void settle("confirm")}
                onCancel={card.state === "proposed" ? () => void settle("decline") : undefined}
            />
            <div aria-live="polite" className="text-sm">
                {card.state === "armed" && (
                    <p className="flex flex-wrap items-center gap-2">
                        <Loader2 aria-hidden className="motion-continuous h-4 w-4 animate-spin" />
                        Starting in a few seconds.
                        <Button type="button" variant="outline" size="sm" className="min-h-11 md:min-h-8" onClick={() => void settle("undo")} disabled={busy}>
                            <Undo2 aria-hidden /> Undo
                        </Button>
                    </p>
                )}
                {card.state === "running" && (
                    <p className="flex items-center gap-2">
                        <Loader2 aria-hidden className="motion-continuous h-4 w-4 animate-spin" /> Working on it…
                    </p>
                )}
                {card.state === "done" && (
                    <p className="flex flex-wrap items-center gap-2 text-[#075A39] dark:text-emerald-300">
                        <CheckCircle2 aria-hidden className="h-4 w-4" /> {card.note ?? "Done."}
                        {card.reversible && (
                            <Button type="button" variant="outline" size="sm" className="min-h-11 text-foreground md:min-h-8" onClick={() => void settle("undo")} disabled={busy}>
                                <Undo2 aria-hidden /> Put it back
                            </Button>
                        )}
                    </p>
                )}
                {card.state === "undone" && <p className="text-muted-foreground">Put back.</p>}
                {(card.state === "failed" || card.state === "outcome_unknown") && (
                    <p className="flex items-center gap-2 text-destructive">
                        <CircleAlert aria-hidden className="h-4 w-4" /> {card.error ?? "It did not happen."}
                    </p>
                )}
                {error && (
                    <p role="alert" className="text-destructive">
                        {error}
                    </p>
                )}
            </div>
        </div>
    );
}

export default SettingsCardPanel;
