/**
 * Answering an action card exactly once from this phone.
 *
 * The server already makes Confirm a compare-and-swap on the card's state
 * and binds it to the payload version (api/services/workflow/actions.py), so
 * the same press from two devices arms the card once. This is the phone's
 * half: a double tap, a tap during a slow network, or the dock and the
 * thread showing the same card must send one request, not two -- a second
 * request would come back as a confusing 409 ("This changed since you looked
 * at it") for something the person did once.
 *
 * - While an answer for a card is in flight, any further answer for that
 *   card returns the same promise (no second request).
 * - After a Confirm succeeded, Confirm for the same card and version is
 *   answered from memory; only Undo can follow.
 * - A failed request is forgotten, so the person can try again.
 */
import type { TimelineEvent } from '@/client/types.gen';

export type Verb = 'confirm' | 'decline' | 'undo';

export type SettleRequest = { event_id: number; verb: Verb; version?: string | null };

export type Settle = (request: SettleRequest) => Promise<TimelineEvent>;

export type Settler = {
    answer(eventId: number, verb: Verb, version?: string | null): Promise<TimelineEvent>;
    pending(eventId: number): boolean;
};

export function createSettler(settle: Settle): Settler {
    const inflight = new Map<number, Promise<TimelineEvent>>();
    const answered = new Map<string, TimelineEvent>();

    return {
        pending(eventId) {
            return inflight.has(eventId);
        },
        answer(eventId, verb, version) {
            const running = inflight.get(eventId);
            if (running) return running;
            const key = `${eventId}:${verb}:${version ?? ''}`;
            if (verb !== 'undo' && answered.has(key)) return Promise.resolve(answered.get(key) as TimelineEvent);
            const request: SettleRequest = { event_id: eventId, verb };
            // A Confirm names the exact version the person read (task ledger).
            if (verb === 'confirm' && version) request.version = version;
            const promise = settle(request)
                .then((event) => {
                    answered.set(key, event);
                    return event;
                })
                .finally(() => {
                    inflight.delete(eventId);
                });
            inflight.set(eventId, promise);
            return promise;
        },
    };
}
