'use client';

/**
 * Images were asked for and no provider is ready: this card, in the thread
 * that asked, is where one is chosen and its key pasted. Never "go to
 * Settings" -- the person is here, so the way past the wall is here too.
 *
 * The key goes straight to the workspace's key vault (PUT /images/provider,
 * admins only, as with every key) and is never shown again: the card reads
 * back at most its last four characters. Once connected, the card sends the
 * original request on again by itself, so nobody retypes it.
 *
 * See api/services/images/offer.py for the row this renders.
 */

import { Check, ImageIcon, KeyRound, Loader2 } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import {
    connectImageProviderApiV1ImagesProviderPut,
    imageProvidersApiV1ImagesProvidersGet,
} from '@/client/sdk.gen';
import type { ImageProvidersResponse, ImageProviderState, TimelineEvent } from '@/client/types.gen';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { detailFromError } from '@/lib/apiError';
import { useAuth } from '@/lib/auth';
import { cn } from '@/lib/utils';

export type ImageProviderOffer = {
    /** The request to send on again once connected. */
    request?: string;
    /** Why the card came back (a key its vendor refused), when it did. */
    reason?: string;
    /** The provider the reason is about, to preselect it. */
    provider?: string | null;
};

export function providerOfferOf(event: TimelineEvent): ImageProviderOffer {
    return (event.payload ?? {}) as ImageProviderOffer;
}

function whose(state: ImageProviderState): string {
    if (state.source === 'platform') return "on Decibyl's key";
    if (state.masked_key) return `on your key ${state.masked_key}`;
    return 'on your key';
}

export function ImageProviderCard({
    event,
    onConnected,
}: {
    event: TimelineEvent;
    /** Sends the original request on again, as the person's own line. */
    onConnected?: (request: string) => void | Promise<void>;
}) {
    const offer = providerOfferOf(event);
    const { user, loading: authLoading } = useAuth();
    const hasFetched = useRef(false);
    const [status, setStatus] = useState<ImageProvidersResponse | null>(null);
    const [loadError, setLoadError] = useState<string | null>(null);
    const [picked, setPicked] = useState<string | null>(offer.provider ?? null);
    const [key, setKey] = useState('');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [connected, setConnected] = useState<{ label: string; message: string } | null>(null);
    const [resent, setResent] = useState(false);

    useEffect(() => {
        if (authLoading || !user || hasFetched.current) return;
        hasFetched.current = true;
        void (async () => {
            const response = await imageProvidersApiV1ImagesProvidersGet();
            if (response.error || !response.data) {
                setLoadError(detailFromError(response.error, 'Could not read the image providers'));
                return;
            }
            setStatus(response.data);
            if (!offer.provider && response.data.chosen) setPicked(response.data.chosen);
        })();
    }, [authLoading, user, offer.provider]);

    const providers = status?.providers ?? [];
    const current = providers.find((p) => p.provider === picked) ?? null;
    // A refused key comes back on this card: the provider is "ready" on
    // paper, but the key it holds is the one that was refused.
    const refused = Boolean(offer.reason) && current?.provider === offer.provider;
    const canUseExisting = Boolean(current?.ready) && !refused;
    const mayConnect = Boolean(current) && (key.trim().length > 0 || canUseExisting);

    const connect = async () => {
        if (!current) return;
        setBusy(true);
        setError(null);
        const response = await connectImageProviderApiV1ImagesProviderPut({
            body: { provider: current.provider, api_key: key.trim() || null, verify: true },
        });
        setBusy(false);
        // Nothing typed here outlives the request, whatever the answer.
        setKey('');
        if (response.error || !response.data) {
            setError(detailFromError(response.error, 'Could not connect that'));
            return;
        }
        setStatus(response.data);
        setConnected({
            label: current.label,
            message: response.data.verification_message || '',
        });
        const request = (offer.request ?? '').trim();
        if (request && onConnected) {
            await onConnected(request);
            setResent(true);
        }
    };

    const alreadyReady =
        !connected && !offer.reason && status?.ready && status.chosen
            ? providers.find((p) => p.provider === status.chosen) ?? null
            : null;

    return (
        <div
            className="rounded-lg border border-border bg-card p-4"
            role="group"
            aria-label="Choose how to make images"
            data-testid="image-provider-card"
            data-state={connected || alreadyReady ? 'connected' : 'choosing'}
        >
            <p className="flex items-start gap-2 text-sm font-medium">
                <ImageIcon aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-primary" />
                <span>Choose how to make images</span>
            </p>
            {offer.reason ? (
                <p className="mt-1 pl-6 text-sm text-amber-700 dark:text-amber-400" role="status">
                    {offer.reason}
                </p>
            ) : (
                <p className="mt-1 pl-6 text-sm text-muted-foreground">
                    Images are made on an image provider&apos;s account. Pick one and paste its key.
                </p>
            )}

            {loadError && (
                <p role="alert" className="mt-3 pl-6 text-sm text-destructive">
                    {loadError}
                </p>
            )}
            {!status && !loadError && (
                <p className="mt-3 flex items-center gap-2 pl-6 text-sm text-muted-foreground">
                    <Loader2 aria-hidden className="motion-continuous h-4 w-4 animate-spin" />
                    Reading what is connected…
                </p>
            )}

            {connected ? (
                <div className="mt-3 pl-6 text-sm">
                    <p className="flex items-center gap-1.5">
                        <Check aria-hidden className="h-4 w-4 text-emerald-600" />
                        <span className="font-medium">{connected.label} is connected.</span>
                    </p>
                    {connected.message && (
                        <p className="mt-1 text-muted-foreground">{connected.message}</p>
                    )}
                    {resent && (
                        <p className="mt-1 text-muted-foreground">Your request was sent again.</p>
                    )}
                </div>
            ) : alreadyReady ? (
                <div className="mt-3 flex flex-wrap items-center gap-3 pl-6 text-sm">
                    <p className="flex items-center gap-1.5">
                        <Check aria-hidden className="h-4 w-4 text-emerald-600" />
                        <span>
                            Images are made with {alreadyReady.label}, {whose(alreadyReady)}.
                        </span>
                    </p>
                    {/* Connected since the card went up (by somebody else,
                        or elsewhere): the request can still go on from here. */}
                    {offer.request && onConnected && !resent && (
                        <Button
                            type="button"
                            size="sm"
                            variant="outline"
                            onClick={async () => {
                                await onConnected(offer.request!.trim());
                                setResent(true);
                            }}
                        >
                            Send my request again
                        </Button>
                    )}
                    {resent && <span className="text-muted-foreground">Sent again.</span>}
                </div>
            ) : status ? (
                <form
                    className="mt-3 flex flex-col gap-3 pl-6"
                    onSubmit={(e) => {
                        e.preventDefault();
                        void connect();
                    }}
                >
                    <div role="radiogroup" aria-label="Image provider" className="flex flex-col gap-2 sm:flex-row">
                        {providers.map((p) => (
                            <button
                                key={p.provider}
                                type="button"
                                role="radio"
                                aria-checked={picked === p.provider}
                                onClick={() => {
                                    setPicked(p.provider);
                                    setError(null);
                                }}
                                className={cn(
                                    'flex-1 rounded-md border px-3 py-2 text-left text-sm transition-colors',
                                    picked === p.provider
                                        ? 'border-primary bg-primary/5'
                                        : 'border-border hover:bg-muted',
                                )}
                            >
                                <span className="block font-medium">{p.label}</span>
                                <span className="block text-xs text-muted-foreground">{p.blurb}</span>
                                {p.ready && !(refused && p.provider === offer.provider) && (
                                    <span className="mt-1 block text-xs text-emerald-700 dark:text-emerald-400">
                                        Ready {whose(p)}
                                    </span>
                                )}
                            </button>
                        ))}
                    </div>

                    {current && (
                        <div className="flex flex-col gap-1">
                            <Label htmlFor={`image-key-${event.id}`} className="text-xs">
                                {current.key_label}
                                {canUseExisting ? ' (optional: leave empty to use the one you have)' : ''}
                            </Label>
                            <Input
                                id={`image-key-${event.id}`}
                                type="password"
                                autoComplete="off"
                                value={key}
                                onChange={(e) => setKey(e.target.value)}
                                disabled={!status.encryption_configured}
                            />
                            <p className="text-xs text-muted-foreground">{current.key_hint}</p>
                            <p className="flex items-center gap-1 text-xs text-muted-foreground">
                                <KeyRound aria-hidden className="h-3 w-3" />
                                Stored encrypted for this workspace. It never appears in this chat.
                            </p>
                        </div>
                    )}
                    {!status.encryption_configured && (
                        <p className="text-xs text-amber-700 dark:text-amber-400">
                            Keys cannot be stored on this server yet: its encryption secret is not set.
                        </p>
                    )}

                    <div className="flex items-center gap-3">
                        <Button type="submit" size="sm" disabled={busy || !mayConnect}>
                            {busy ? 'Connecting…' : 'Connect'}
                        </Button>
                        {error && (
                            <span role="alert" className="text-sm text-destructive">
                                {error}
                            </span>
                        )}
                    </div>
                </form>
            ) : null}
        </div>
    );
}
