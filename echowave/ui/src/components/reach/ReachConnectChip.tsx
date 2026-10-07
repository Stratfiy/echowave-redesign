'use client';

/**
 * Connect an outside AI tool or an ordering app, here in the thread.
 *
 * Decibyl puts this chip where the need came up (stream `reach`); nobody is
 * sent to another screen. An outside tool is connected by typing its
 * server address (and a token, if it uses one) into the chip; a server or
 * an ordering app that signs people in opens its own screen in a new tab,
 * and "I've signed in" checks it from here.
 *
 * Pressing it connects the person pressing it -- their own account, never
 * the person it was offered to -- and an app Decibyl cannot reach yet says
 * "needs setup" with the reason, with no button that would pretend.
 *
 * See api/services/reach/chips.py for the row this renders.
 */

import { Check, Loader2, Plug, ShieldAlert } from 'lucide-react';
import { useId, useState } from 'react';

import {
    connectApiV1ReachConnectionsPost,
    refreshConnectionApiV1ReachConnectionsConnectionIdRefreshPost,
} from '@/client/sdk.gen';
import type { ReachConnection, TimelineEvent } from '@/client/types.gen';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { detailFromError } from '@/lib/apiError';

export type ReachChip = {
    reach_kind?: 'tool' | 'ordering';
    provider?: string;
    name?: string;
    server_url?: string | null;
    state?: 'available' | 'needs_setup' | string;
    reason?: string | null;
    why?: string;
};

export function chipOf(event: TimelineEvent): ReachChip {
    return (event.payload ?? {}) as ReachChip;
}

function connectedLine(connection: ReachConnection): string {
    const total = connection.tools.length;
    const reads = connection.tools.filter((t) => t.read).length;
    if (connection.kind === 'ordering') return `${connection.name} is connected to your account.`;
    return `${connection.name} is connected: ${total} ${total === 1 ? 'tool' : 'tools'} (${reads} read-only). Anything that changes something asks you first.`;
}

export function ReachConnectChip({ event }: { event: TimelineEvent }) {
    const chip = chipOf(event);
    const name = chip.name || chip.provider || 'this tool';
    const isTool = chip.reach_kind !== 'ordering';
    const ids = { url: useId(), token: useId() };
    const [url, setUrl] = useState(chip.server_url ?? '');
    const [token, setToken] = useState('');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [pending, setPending] = useState<ReachConnection | null>(null);
    const [done, setDone] = useState<ReachConnection | null>(null);

    const settle = (connection: ReachConnection) => {
        if (connection.status === 'connected') {
            setDone(connection);
            setPending(null);
        } else if (connection.status === 'pending') {
            setPending(connection);
        } else {
            setError(connection.last_error || `${name} could not be connected.`);
        }
    };

    const connect = async () => {
        setBusy(true);
        setError(null);
        const result = await connectApiV1ReachConnectionsPost({
            body: isTool
                ? { kind: 'tool', name, server_url: url.trim(), token: token.trim() || null }
                : { kind: 'ordering', provider: chip.provider },
        });
        setBusy(false);
        setToken('');
        if (result.error || !result.data) {
            setError(detailFromError(result.error, `${name} could not be connected`));
            return;
        }
        if (result.data.authorize_url) {
            // The server's own sign-in, in a new tab; the thread stays here.
            window.open(result.data.authorize_url, '_blank', 'noopener,noreferrer');
        }
        settle(result.data.connection);
    };

    const check = async () => {
        if (!pending) return;
        setBusy(true);
        setError(null);
        const result = await refreshConnectionApiV1ReachConnectionsConnectionIdRefreshPost({
            path: { connection_id: pending.id },
        });
        setBusy(false);
        if (result.error || !result.data) {
            setError(
                detailFromError(result.error, `${name} does not look signed in yet. Finish on its screen, then check again.`),
            );
            return;
        }
        settle(result.data);
    };

    const needsSetup = chip.state === 'needs_setup';

    return (
        <section
            className="rounded-lg border border-border bg-card p-4"
            aria-label={`Connect ${name}`}
            data-testid="reach-connect-chip"
            data-state={done ? 'connected' : needsSetup ? 'needs_setup' : pending ? 'signing_in' : 'available'}
        >
            <div className="flex items-start gap-3">
                <Plug aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                <div className="min-w-0 flex-1">
                    <p className="text-sm font-medium">
                        {needsSetup ? name : `Connect ${name}`}
                        {needsSetup && (
                            <span className="ml-2 rounded-md bg-amber-50 px-1.5 py-0.5 text-xs font-medium text-[#705500] dark:bg-amber-950 dark:text-amber-300">
                                Needs setup
                            </span>
                        )}
                    </p>
                    {chip.why && !needsSetup && <p className="mt-0.5 text-sm text-muted-foreground">{chip.why}</p>}
                    {needsSetup && chip.reason && (
                        <p className="mt-0.5 flex items-start gap-1.5 text-sm text-muted-foreground">
                            <ShieldAlert aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                            {chip.reason}
                        </p>
                    )}
                    {!needsSetup && !done && (
                        <p className="mt-0.5 text-xs text-muted-foreground">
                            Connects your own account. Only you can see or use it.
                        </p>
                    )}
                </div>
            </div>

            {done && (
                <p role="status" className="mt-3 flex items-start gap-1.5 text-sm">
                    <Check aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-emerald-600" />
                    {connectedLine(done)}
                </p>
            )}

            {!needsSetup && !done && !pending && (
                <form
                    className="mt-3 flex flex-col gap-2"
                    onSubmit={(e) => {
                        e.preventDefault();
                        void connect();
                    }}
                >
                    {isTool && (
                        <>
                            <label htmlFor={ids.url} className="text-xs font-medium">
                                Server address
                            </label>
                            <Input
                                id={ids.url}
                                type="url"
                                inputMode="url"
                                required
                                placeholder="https://…/mcp"
                                value={url}
                                onChange={(e) => setUrl(e.target.value)}
                                className="min-h-11 md:min-h-9"
                            />
                            <label htmlFor={ids.token} className="text-xs font-medium">
                                Token <span className="font-normal text-muted-foreground">(only if the tool gave you one)</span>
                            </label>
                            <Input
                                id={ids.token}
                                type="password"
                                autoComplete="off"
                                value={token}
                                onChange={(e) => setToken(e.target.value)}
                                className="min-h-11 md:min-h-9"
                            />
                        </>
                    )}
                    <div>
                        <Button type="submit" className="min-h-11 md:min-h-9" disabled={busy || (isTool && !url.trim())}>
                            {busy ? <Loader2 aria-hidden className="mr-1 h-4 w-4 animate-spin" /> : <Plug aria-hidden className="mr-1 h-4 w-4" />}
                            {busy ? 'Connecting…' : isTool ? 'Connect' : `Sign in to ${name}`}
                        </Button>
                    </div>
                </form>
            )}

            {pending && !done && (
                <div className="mt-3 flex flex-wrap items-center gap-2">
                    <p className="text-sm text-muted-foreground">Sign in on {name}&apos;s screen (it opened in a new tab), then:</p>
                    <Button type="button" variant="outline" className="min-h-11 md:min-h-9" disabled={busy} onClick={() => void check()}>
                        {busy ? <Loader2 aria-hidden className="mr-1 h-4 w-4 animate-spin" /> : <Check aria-hidden className="mr-1 h-4 w-4" />}
                        {busy ? 'Checking…' : "I've signed in"}
                    </Button>
                </div>
            )}

            {error && (
                <p role="alert" className="mt-2 text-sm text-destructive">
                    {error}
                </p>
            )}
        </section>
    );
}

export default ReachConnectChip;
