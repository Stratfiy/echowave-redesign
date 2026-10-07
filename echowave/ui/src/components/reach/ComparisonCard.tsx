'use client';

/**
 * Prices and coupons compared across the apps this person connected
 * (stream `reach`). Says which apps were compared, which were not and why,
 * and when -- the same facts Decibyl's sentence carries, on screen.
 */

import type { TimelineEvent } from '@/client/types.gen';

import { rupees } from './money';

type Line = { asked: string; found?: string | null; store?: string | null; price_paise?: number | null };
type Compared = {
    app: string;
    lines: Line[];
    listed_total_paise: number;
    missing: string[];
    coupons: { code: string; description?: string }[];
};
type Payload = {
    items?: string[];
    compared?: Compared[];
    not_compared?: { app: string; why: string }[];
    at?: string;
};

export function ComparisonCard({ event }: { event: TimelineEvent }) {
    const payload = (event.payload ?? {}) as Payload;
    const compared = payload.compared ?? [];
    const at = payload.at ? new Date(payload.at) : null;
    return (
        <section className="rounded-lg border border-border bg-card p-4" aria-label="Price comparison" data-testid="comparison-card">
            <p className="text-sm font-medium">
                Compared {compared.map((c) => c.app).join(', ') || 'nothing'}
                {at && (
                    <>
                        {' · '}
                        <time dateTime={payload.at} className="font-normal text-muted-foreground">
                            {at.toLocaleString(undefined, { day: 'numeric', month: 'short', hour: 'numeric', minute: '2-digit' })}
                        </time>
                    </>
                )}
            </p>
            <div className="mt-2 flex flex-col gap-3">
                {compared.map((app) => (
                    <div key={app.app} className="min-w-0">
                        <p className="text-sm font-medium">
                            {app.app}: {rupees(app.listed_total_paise)} listed
                            {app.missing.length > 0 && (
                                <span className="font-normal text-muted-foreground"> · not found: {app.missing.join(', ')}</span>
                            )}
                        </p>
                        <ul className="mt-1 flex flex-col gap-0.5 text-sm text-muted-foreground">
                            {app.lines
                                .filter((l) => l.price_paise != null)
                                .map((l) => (
                                    <li key={l.asked} className="break-words">
                                        {l.found} {l.store ? `(${l.store})` : ''} — {rupees(l.price_paise)}
                                    </li>
                                ))}
                        </ul>
                        {app.coupons.length > 0 && (
                            <p className="mt-1 text-xs text-muted-foreground">
                                Coupons: {app.coupons.map((c) => `${c.code}${c.description ? ` (${c.description})` : ''}`).join('; ')}
                            </p>
                        )}
                    </div>
                ))}
            </div>
            {(payload.not_compared ?? []).length > 0 && (
                <p className="mt-2 text-xs text-muted-foreground">
                    Not compared: {(payload.not_compared ?? []).map((n) => `${n.app} (${n.why})`).join('; ')}
                </p>
            )}
            <p className="mt-2 text-xs text-muted-foreground">
                Listed prices. The order card&apos;s total, with delivery, taxes and any coupon, is the figure that counts.
            </p>
        </section>
    );
}

export default ComparisonCard;
