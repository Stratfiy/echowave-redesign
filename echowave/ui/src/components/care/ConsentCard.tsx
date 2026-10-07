"use client";

/**
 * A care consent card, answered where the person already is.
 *
 * The card is the controls action card (services/workflow/actions.py, the
 * same row Chat shows): Confirm arms it, Undo is there for the window, and
 * it runs once. This wrapper only re-reads the row when the window closes,
 * so the screen moves to what happened without anybody reloading it.
 */

import { useCallback, useEffect, useState } from "react";

import { careCardApiV1CareCardsEventIdGet } from "@/client/sdk.gen";
import type { TimelineEvent } from "@/client/types.gen";
import { ActionCard } from "@/components/workflow/ActionCard";

export function ConsentCard({
    card,
    onChanged,
}: {
    card: TimelineEvent;
    /** Called with the card's state whenever it moves, so the panel can refetch. */
    onChanged?: (state: string) => void;
}) {
    const [event, setEvent] = useState<TimelineEvent>(card);
    useEffect(() => setEvent(card), [card]);

    const refetch = useCallback(async () => {
        const response = await careCardApiV1CareCardsEventIdGet({ path: { event_id: event.id } });
        if (response.data) {
            setEvent(response.data);
            onChanged?.(String((response.data.payload as { state?: string })?.state ?? ""));
        }
    }, [event.id, onChanged]);

    return (
        <div data-testid="care-consent-card">
            <ActionCard
                event={event}
                onSettled={(next) => {
                    setEvent(next);
                    onChanged?.(String((next.payload as { state?: string })?.state ?? ""));
                }}
                onFired={() => void refetch()}
            />
        </div>
    );
}

export default ConsentCard;
