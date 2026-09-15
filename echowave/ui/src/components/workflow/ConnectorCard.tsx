'use client';

/**
 * Decibyl offered an app; this is the card that connects it.
 *
 * What the Integrations screen shows for one app -- the logo, the one
 * line, how many tools it brings -- inside the conversation that needed
 * it, so nobody is sent off to find a screen. The button mints the
 * sign-in link when it is pressed (links are short-lived, so one issued
 * with the card would be dead by the time anybody read it) and opens the
 * vendor's own screen in a new tab.
 *
 * Nothing here connects anything. The consent is the vendor's sign-in,
 * and connecting binds the whole account, so the endpoint behind this is
 * an admin's: a member sees the refusal in plain words rather than a
 * button that does nothing.
 *
 * See api/services/workflow/connector_offer.py for the row this renders.
 */

import { ArrowUpRight, Check, Loader2, Plug } from 'lucide-react';
import { useState } from 'react';

import {
    startConnectingApiV1ConnectorsSlugConnectPost,
    syncAppToolsApiV1ConnectorsSlugToolsSyncPost,
} from '@/client/sdk.gen';
import type { TimelineEvent } from '@/client/types.gen';
import { ConnectorLogo } from '@/components/integrations/ConnectorRow';
import { Button } from '@/components/ui/button';
import { detailFromError } from '@/lib/apiError';

export type ConnectorOffer = {
    app?: string;
    name?: string;
    description?: string;
    logo?: string | null;
    tools_count?: number;
    why?: string;
};

export function offerOf(event: TimelineEvent): ConnectorOffer {
    return (event.payload ?? {}) as ConnectorOffer;
}

export function ConnectorCard({ event }: { event: TimelineEvent }) {
    const offer = offerOf(event);
    const name = offer.name || offer.app || 'this app';
    const [busy, setBusy] = useState(false);
    const [opened, setOpened] = useState(false);
    const [done, setDone] = useState<string | null>(null);
    const [error, setError] = useState<string | null>(null);

    const connect = async () => {
        if (!offer.app) return;
        setBusy(true);
        setError(null);
        const result = await startConnectingApiV1ConnectorsSlugConnectPost({
            path: { slug: offer.app },
        });
        setBusy(false);
        if (result.error) {
            setError(detailFromError(result.error, `Could not start signing in to ${name}`));
            return;
        }
        const url = result.data?.connect_url;
        if (!url) {
            setError(`${name} did not give us a sign-in link. Try again in a minute.`);
            return;
        }
        setOpened(true);
        // A new tab, not this one: the thread stays where it is, and the
        // person comes back to it with the app connected.
        window.open(url, '_blank', 'noopener,noreferrer');
    };

    // Pressed when they come back. Signing in happens on the vendor's own
    // screen and we are never told, so this is the person saying so -- and
    // it is what makes the app's tools, rather than leaving somebody to find
    // out later that connected and usable were two different things.
    const finish = async () => {
        if (!offer.app) return;
        setBusy(true);
        setError(null);
        const result = await syncAppToolsApiV1ConnectorsSlugToolsSyncPost({
            path: { slug: offer.app },
        });
        setBusy(false);
        if (result.error) {
            setError(
                detailFromError(
                    result.error,
                    `${name} does not look connected yet. Finish signing in, then press this again.`,
                ),
            );
            return;
        }
        const created = result.data?.created ?? 0;
        const total = result.data?.total ?? 0;
        setDone(
            created > 0
                ? `${name} is connected. ${created} ${created === 1 ? 'tool' : 'tools'} added to Your tools.`
                : `${name} is connected, with ${total} ${total === 1 ? 'tool' : 'tools'}.`,
        );
    };

    return (
        <div
            className="rounded-lg border border-border bg-card p-4"
            role="group"
            aria-label={`Connect ${name}`}
            data-testid="connector-card"
        >
            <div className="flex items-start gap-3">
                <ConnectorLogo connector={{ name, logo: offer.logo ?? null }} />
                <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium">{name}</p>
                    {offer.description && (
                        <p className="mt-0.5 line-clamp-2 text-sm text-muted-foreground">
                            {offer.description}
                        </p>
                    )}
                    {offer.tools_count ? (
                        <p className="mt-0.5 text-xs text-muted-foreground">
                            {offer.tools_count} {offer.tools_count === 1 ? 'tool' : 'tools'}
                        </p>
                    ) : null}
                </div>
                <div className="shrink-0">
                    {done ? (
                        <span className="inline-flex items-center gap-1.5 rounded-md bg-emerald-50 px-2.5 py-1.5 text-xs font-medium text-emerald-700 dark:bg-emerald-950 dark:text-emerald-300">
                            <Check aria-hidden className="h-3.5 w-3.5" />
                            Added
                        </span>
                    ) : opened ? (
                        <Button size="sm" variant="outline" disabled={busy} onClick={() => void finish()}>
                            {busy ? (
                                <Loader2 aria-hidden className="mr-1 h-3.5 w-3.5 animate-spin" />
                            ) : (
                                <Check aria-hidden className="mr-1 h-3.5 w-3.5" />
                            )}
                            {busy ? 'Checking…' : "I've signed in"}
                        </Button>
                    ) : (
                        <Button size="sm" disabled={busy || !offer.app} onClick={() => void connect()}>
                            {busy ? (
                                <Loader2 aria-hidden className="mr-1 h-3.5 w-3.5 animate-spin" />
                            ) : (
                                <Plug aria-hidden className="mr-1 h-3.5 w-3.5" />
                            )}
                            {busy ? 'Opening…' : 'Sign in'}
                        </Button>
                    )}
                </div>
            </div>
            {offer.why && <p className="mt-2 text-sm text-muted-foreground">{offer.why}</p>}
            {done ? (
                <p className="mt-2 text-sm text-emerald-700 dark:text-emerald-300">{done}</p>
            ) : opened ? (
                <p className="mt-2 flex items-center gap-1 text-xs text-muted-foreground">
                    <ArrowUpRight aria-hidden className="h-3 w-3" />
                    Finish in the tab that opened, then press I&apos;ve signed in.
                </p>
            ) : null}
            {error && (
                <p role="alert" className="mt-2 text-sm text-destructive">
                    {error}
                </p>
            )}
        </div>
    );
}
