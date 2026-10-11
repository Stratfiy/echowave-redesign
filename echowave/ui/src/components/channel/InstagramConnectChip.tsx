'use client';

/**
 * Connect the workspace's Instagram account, here in the thread.
 *
 * Decibyl puts this where the ask was made ("answer my Instagram DMs");
 * nobody is sent to another screen. The button opens Instagram's own
 * sign-in in a new tab, and "I've signed in" checks from here. Once
 * connected, Decibyl is asked which agent answers.
 *
 * See api/services/messaging/instagram.py for what the button starts.
 */

import { Check, Instagram, Loader2 } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import {
    connectApiV1InstagramConnectPost,
    getAccountApiV1InstagramAccountGet,
} from '@/client/sdk.gen';
import type { InstagramAccountResponse, TimelineEvent } from '@/client/types.gen';
import { Button } from '@/components/ui/button';
import { detailFromError } from '@/lib/apiError';
import { useAuth } from '@/lib/auth';

export function InstagramConnectChip({ event }: { event: TimelineEvent }) {
    const why = String((event.payload ?? {}).why ?? '');
    const [busy, setBusy] = useState(false);
    const [opened, setOpened] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [account, setAccount] = useState<InstagramAccountResponse | null>(null);

    const { user, loading: authLoading } = useAuth();
    const hasFetched = useRef(false);

    // Coming back to the thread: an account connected since shows as such.
    useEffect(() => {
        if (authLoading || !user || hasFetched.current) return;
        hasFetched.current = true;
        let live = true;
        void getAccountApiV1InstagramAccountGet().then((result) => {
            if (live && result.data?.connected) setAccount(result.data);
        });
        return () => {
            live = false;
        };
    }, [authLoading, user]);

    const connect = async () => {
        setBusy(true);
        setError(null);
        const result = await connectApiV1InstagramConnectPost();
        setBusy(false);
        if (result.error || !result.data) {
            setError(detailFromError(result.error, 'Instagram could not be opened'));
            return;
        }
        window.open(result.data.authorize_url, '_blank', 'noopener,noreferrer');
        setOpened(true);
    };

    const check = async () => {
        setBusy(true);
        setError(null);
        const result = await getAccountApiV1InstagramAccountGet();
        setBusy(false);
        if (result.data?.connected) {
            setAccount(result.data);
            return;
        }
        setError('Instagram does not look connected yet. Finish signing in on its screen, then check again.');
    };

    return (
        <div className="rounded-lg border bg-card p-3 text-sm" data-testid="instagram-connect-chip">
            <div className="flex items-center gap-2 font-medium">
                <Instagram className="size-4" aria-hidden />
                Instagram messages
            </div>
            {account ? (
                <p className="mt-2 flex items-center gap-1.5 text-muted-foreground">
                    <Check className="size-4 text-green-600" aria-hidden />
                    @{account.username} is connected.
                    {account.answered_by
                        ? ` ${account.answered_by} answers its messages.`
                        : ' Tell Decibyl which agent should answer.'}
                </p>
            ) : (
                <>
                    <p className="mt-1 text-muted-foreground">
                        {why ||
                            'Sign in with your Instagram professional account so an agent can answer its messages.'}
                    </p>
                    <div className="mt-3 flex flex-wrap gap-2">
                        <Button size="sm" onClick={connect} disabled={busy}>
                            {busy && !opened ? <Loader2 className="mr-1 size-4 animate-spin" aria-hidden /> : null}
                            Connect Instagram
                        </Button>
                        {opened ? (
                            <Button size="sm" variant="outline" onClick={check} disabled={busy}>
                                {busy ? <Loader2 className="mr-1 size-4 animate-spin" aria-hidden /> : null}
                                I&apos;ve signed in
                            </Button>
                        ) : null}
                    </div>
                </>
            )}
            {error ? (
                <p role="alert" className="mt-2 text-destructive">
                    {error}
                </p>
            ) : null}
        </div>
    );
}
