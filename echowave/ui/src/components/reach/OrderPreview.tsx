'use client';

/**
 * The order card, before anything is placed (stream `reach`).
 *
 * The exact order -- each item and quantity, every charge, the total, the
 * address and how it is paid -- read from the person's own draft
 * (``/reach/orders/{id}``) and shown in the shared ActionPreview. Approve
 * sends the card's version and no other: an edit prices the order again
 * and puts up a new card, so an approval never carries over to new figures.
 *
 * The address and payment are not on the shared thread row; a colleague
 * who can see the thread gets "only the person who asked can see this",
 * and the server refuses their approval anyway.
 */

import { Minus, Plus } from 'lucide-react';
import { useEffect, useState } from 'react';

import {
    orderDetailApiV1ReachOrdersOrderIdGet,
    reviseOrderApiV1ReachOrdersOrderIdRevisePost,
    settleActionApiV1TimelineActionsSettlePost,
} from '@/client/sdk.gen';
import type { ReachOrderDetail, TimelineEvent } from '@/client/types.gen';
import { ActionPreview, type ApprovalStatus } from '@/components/shell/ActionPreview';
import { Button } from '@/components/ui/button';
import { detailFromError } from '@/lib/apiError';
import { useFeature } from '@/lib/features';

import { rupees } from './money';

type Payload = { version?: string; args?: { draft?: string } };

/** The bill as the card's content: one line per item, then charges, total. */
export function billLines(order: ReachOrderDetail): string {
    const lines = order.items.map((i) => `${i.quantity} × ${i.name} — ${rupees(i.line_total_paise)}`);
    for (const charge of order.charges) lines.push(`${charge.label} — ${rupees(charge.amount_paise)}`);
    if (order.discount_paise) {
        lines.push(`Discount${order.coupon ? ` (${order.coupon})` : ''} — −${rupees(order.discount_paise)}`);
    }
    lines.push(`Total — ${rupees(order.total_paise)}`);
    return lines.join('\n');
}

export function OrderPreview({
    event,
    onSettled,
    onFired,
}: {
    event: TimelineEvent;
    onSettled?: (event: TimelineEvent) => void;
    /** A new card replaced this one: the thread should refetch. */
    onFired?: () => void;
}) {
    const orderingOn = useFeature('ordering');
    const payload = (event.payload ?? {}) as Payload;
    const draft = payload.args?.draft;
    const [order, setOrder] = useState<ReachOrderDetail | null>(null);
    const [state, setState] = useState<'loading' | 'ready' | 'not_yours' | 'failed'>('loading');
    const [status, setStatus] = useState<ApprovalStatus>('pending');
    const [error, setError] = useState<string | null>(null);
    const [editing, setEditing] = useState(false);
    const [quantities, setQuantities] = useState<Record<string, number>>({});

    useEffect(() => {
        if (!draft) {
            setState('failed');
            return;
        }
        let live = true;
        void orderDetailApiV1ReachOrdersOrderIdGet({ path: { order_id: draft } }).then((result) => {
            if (!live) return;
            if (result.response?.status === 404) setState('not_yours');
            else if (result.error || !result.data) setState('failed');
            else {
                setOrder(result.data);
                setQuantities(Object.fromEntries(result.data.items.map((i) => [i.item_id, i.quantity])));
                setState('ready');
            }
        });
        return () => {
            live = false;
        };
    }, [draft]);

    if (state === 'loading') {
        return (
            <p className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground" aria-busy="true">
                Loading the order…
            </p>
        );
    }
    if (!orderingOn) {
        // Rolled back: the server refuses to place it, so say that rather
        // than show a bill with an Approve button that cannot work.
        return (
            <p className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground" data-testid="order-switched-off">
                {event.summary}. Ordering is switched off here, so nothing will be placed.
            </p>
        );
    }
    if (state === 'not_yours') {
        return (
            <p className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground" data-testid="order-not-yours">
                {event.summary}. Only the person who asked can see the details and approve it.
            </p>
        );
    }
    if (state === 'failed' || !order) {
        return (
            <p role="alert" className="rounded-lg border border-border bg-card p-4 text-sm text-destructive">
                This order could not be loaded. Nothing has been placed.
            </p>
        );
    }

    const settle = async (verb: 'confirm' | 'decline') => {
        setStatus('committing');
        setError(null);
        const result = await settleActionApiV1TimelineActionsSettlePost({
            body: { event_id: event.id, verb, ...(verb === 'confirm' && payload.version ? { version: payload.version } : {}) },
        });
        if (result.error || !result.data) {
            setStatus('pending');
            setError(detailFromError(result.error, 'Could not do that'));
            return;
        }
        setStatus(verb === 'confirm' ? 'approved' : 'cancelled');
        onSettled?.(result.data);
    };

    const saveEdit = async () => {
        setStatus('committing');
        setError(null);
        const result = await reviseOrderApiV1ReachOrdersOrderIdRevisePost({
            path: { order_id: order.id },
            body: { items: Object.entries(quantities).map(([item_id, quantity]) => ({ item_id, quantity })) },
        });
        setStatus('pending');
        if (result.error) {
            setError(detailFromError(result.error, 'That change could not be priced'));
            return;
        }
        setEditing(false);
        onFired?.();
    };

    const where = order.store?.name ? `${order.store.name} on ${order.provider_name}` : order.provider_name;
    return (
        <div data-testid="order-preview">
            <ActionPreview
                preview={{
                    id: String(event.id),
                    version: payload.version ?? order.digest.slice(0, 8),
                    action: `Order from ${where}`,
                    account: `${order.provider_name} (your account)`,
                    recipient: `${order.address.label ?? 'Address'}: ${order.address.line ?? ''}`.trim(),
                    content: billLines(order),
                    timing: 'When you approve, with 10 seconds to undo',
                    consequence: `Paid on ${order.provider_name}: ${order.payment.label ?? 'on the app'}. It cannot be undone once placed.`,
                }}
                status={status}
                onApprove={() => void settle('confirm')}
                onEdit={() => setEditing((on) => !on)}
                onCancel={() => void settle('decline')}
            />
            {editing && (
                <fieldset className="mt-2 rounded-lg border border-border bg-card p-3" aria-label="Change quantities">
                    <legend className="px-1 text-xs font-medium">Change quantities (0 removes an item)</legend>
                    <ul className="flex flex-col gap-2">
                        {order.items.map((item) => (
                            <li key={item.item_id} className="flex items-center justify-between gap-2 text-sm">
                                <span className="min-w-0 break-words">{item.name}</span>
                                <span className="flex shrink-0 items-center gap-1">
                                    <Button
                                        type="button"
                                        size="icon"
                                        variant="outline"
                                        className="h-11 w-11"
                                        aria-label={`One fewer ${item.name}`}
                                        onClick={() =>
                                            setQuantities((q) => ({ ...q, [item.item_id]: Math.max(0, (q[item.item_id] ?? 0) - 1) }))
                                        }
                                    >
                                        <Minus aria-hidden className="h-4 w-4" />
                                    </Button>
                                    <span className="w-6 text-center tabular-nums" aria-live="polite">
                                        {quantities[item.item_id] ?? 0}
                                    </span>
                                    <Button
                                        type="button"
                                        size="icon"
                                        variant="outline"
                                        className="h-11 w-11"
                                        aria-label={`One more ${item.name}`}
                                        onClick={() =>
                                            setQuantities((q) => ({ ...q, [item.item_id]: Math.min(50, (q[item.item_id] ?? 0) + 1) }))
                                        }
                                    >
                                        <Plus aria-hidden className="h-4 w-4" />
                                    </Button>
                                </span>
                            </li>
                        ))}
                    </ul>
                    <Button type="button" className="mt-3 min-h-11 md:min-h-9" disabled={status === 'committing'} onClick={() => void saveEdit()}>
                        Price it again
                    </Button>
                </fieldset>
            )}
            {error && (
                <p role="alert" className="mt-2 text-sm text-destructive">
                    {error}
                </p>
            )}
        </div>
    );
}

export default OrderPreview;
