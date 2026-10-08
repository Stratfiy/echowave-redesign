/**
 * "Connect Gmail" in the thread (connector_offer, AGENTS.md: never send
 * anybody to another screen to finish something). The sign-in opens in an
 * in-app browser and comes back to the same thread; then the app's tools are
 * synced and the chip says whether it worked.
 *
 * Two kinds of chip: a connected app (`connector_offered`, Composio) and an
 * outside tool or ordering app (`reach_connect_offered`, flag outside_tools
 * or ordering).
 */
import * as Linking from 'expo-linking';
import * as WebBrowser from 'expo-web-browser';
import { useState } from 'react';
import { View } from 'react-native';

import {
    connectApiV1ReachConnectionsPost,
    refreshConnectionApiV1ReachConnectionsConnectionIdRefreshPost,
    startConnectingApiV1ConnectorsSlugConnectPost,
    startConnectingForMeApiV1ConnectorsSlugConnectMinePost,
    syncAppToolsApiV1ConnectorsSlugToolsSyncPost,
} from '@/client/sdk.gen';
import { Button, Card, Notice, Txt } from '@/components/ui';
import { ApiError, call } from '@/lib/api';
import { payloadOf, type Row } from '@/lib/chat/events';
import { useFeature } from '@/lib/features';
import { useI18n } from '@/lib/i18n';

type Phase = 'idle' | 'opening' | 'returned' | 'ready' | 'not_yet' | 'error';

async function openSignIn(url: string): Promise<void> {
    // Comes back to the app on decibyl://connected; a provider that does not
    // redirect is closed by the person, and the result is checked either way.
    await WebBrowser.openAuthSessionAsync(url, Linking.createURL('connected'));
}

export function ConnectChip({ row }: { row: Row }) {
    const { t } = useI18n();
    const perPerson = useFeature('connections_per_person');
    const p = payloadOf(row);
    const reach = row.kind === 'reach_connect_offered';
    const name = String(p.name ?? p.app ?? row.summary.replace(/^Connect /, ''));
    const [phase, setPhase] = useState<Phase>('idle');
    const [message, setMessage] = useState<string | null>(null);
    const [reachId, setReachId] = useState<string | null>(null);

    const check = async () => {
        try {
            if (reach) {
                if (!reachId) return;
                const result = await call(refreshConnectionApiV1ReachConnectionsConnectionIdRefreshPost({ path: { connection_id: reachId } }));
                setPhase(result.status === 'connected' ? 'ready' : 'not_yet');
                return;
            }
            await call(syncAppToolsApiV1ConnectorsSlugToolsSyncPost({ path: { slug: String(p.app) } }));
            setPhase('ready');
        } catch (e) {
            if (e instanceof ApiError && e.status === 409) setPhase('not_yet');
            else {
                setPhase('error');
                setMessage(e instanceof Error ? e.message : String(e));
            }
        }
    };

    const connect = async () => {
        setPhase('opening');
        setMessage(null);
        try {
            if (reach) {
                const result = await call(
                    connectApiV1ReachConnectionsPost({
                        body: {
                            kind: p.reach_kind === 'ordering' ? 'ordering' : 'tool',
                            provider: (p.provider as string) ?? null,
                            name: (p.name as string) ?? null,
                            server_url: (p.server_url as string) ?? null,
                        },
                    }),
                );
                setReachId(result.connection.id);
                if (result.authorize_url) await openSignIn(result.authorize_url);
            } else {
                const start = perPerson ? startConnectingForMeApiV1ConnectorsSlugConnectMinePost : startConnectingApiV1ConnectorsSlugConnectPost;
                const link = await call(start({ path: { slug: String(p.app) } }));
                await openSignIn(link.connect_url);
            }
            setPhase('returned');
            await check();
        } catch (e) {
            if (e instanceof ApiError && e.status === 409) setPhase('ready');
            else if (e instanceof ApiError && e.status === 403) {
                setPhase('error');
                setMessage(t('connect.adminOnly'));
            } else {
                setPhase('error');
                setMessage(e instanceof Error ? e.message : String(e));
            }
        }
    };

    return (
        <Card testID={`connect-${row.id}`}>
            <Txt weight="700">{t('connect.title', { name })}</Txt>
            {typeof p.why === 'string' && p.why ? <Txt size={14} tone="ink2">{p.why}</Txt> : null}
            {typeof p.reason === 'string' && p.state === 'needs_setup' ? <Notice text={p.reason} /> : null}
            {phase === 'ready' ? (
                <Txt weight="600" tone="live">
                    {t('connect.ready', { name })}
                </Txt>
            ) : (
                <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap' }}>
                    <Button label={t('connect.button')} onPress={connect} busy={phase === 'opening'} testID={`connect-${row.id}-start`} compact />
                    {phase === 'returned' || phase === 'not_yet' ? (
                        <Button label={t('connect.done')} kind="secondary" onPress={check} compact />
                    ) : null}
                </View>
            )}
            {phase === 'not_yet' ? <Notice text={t('connect.notYet', { name })} tone="haldi" /> : null}
            {message ? <Notice text={message} tone="bad" /> : null}
        </Card>
    );
}
