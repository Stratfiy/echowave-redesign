/**
 * Privacy (screen 25) natively: download my data, delete my data, and stop
 * syncing contacts. Deletion is never one tap: the phrase asks, and a card
 * with the exact effect must then be confirmed once (POST
 * /me/privacy/deletion, then the settings card's settle), exactly as on the
 * web. The export downloads as a file the phone can save or share.
 */
import { useLoad } from '@/lib/useLoad';
import { File, Paths } from 'expo-file-system';
import { Stack } from 'expo-router';
import * as Sharing from 'expo-sharing';
import { useCallback, useRef, useState } from 'react';
import { Platform, View } from 'react-native';

import {
    myPrivacyApiV1MePrivacyGet,
    requestMyDeletionApiV1MePrivacyDeletionPost,
    requestMyExportApiV1MePrivacyExportPost,
    settingsCardApiV1MeSettingsCardsEventIdGet,
    settleSettingsCardApiV1MeSettingsCardsEventIdSettlePost,
} from '@/client/sdk.gen';
import type { DataRequest, PrivacyOverview, SettingsCard } from '@/client/types.gen';
import { Button, Card, Divider, Field, Loading, Notice, Row, Screen, Section, Txt } from '@/components/ui';
import { ApiError, call } from '@/lib/api';
import { authHeader } from '@/lib/auth/token';
import { cancelContactsSync } from '@/lib/background/tasks';
import { hasConsent } from '@/lib/contacts/consent';
import { stopSyncing } from '@/lib/contacts/sync';
import { apiBaseUrl } from '@/lib/config';
import { formatTime, useI18n } from '@/lib/i18n';
import { createPeopleApi } from '@/lib/people/api';
import { plain } from '@/lib/storage';

async function downloadExport(request: DataRequest): Promise<void> {
    const response = await fetch(`${apiBaseUrl()}/api/v1/me/privacy/requests/${request.id}/download`, { headers: authHeader() });
    if (!response.ok) throw new Error(`The file could not be downloaded (${response.status}).`);
    const text = await response.text();
    const name = `decibyl-export-${request.id}.json`;
    if (Platform.OS === 'web') {
        const url = URL.createObjectURL(new Blob([text], { type: 'application/json' }));
        globalThis.open?.(url, '_blank');
        return;
    }
    const file = new File(Paths.cache, name);
    file.write(text);
    await Sharing.shareAsync(file.uri, { mimeType: 'application/json', UTI: 'public.json', dialogTitle: name });
}

async function cardFor(request: DataRequest): Promise<SettingsCard | null> {
    if (!request.card_event_id || !request.card_organization_id) return null;
    return call(
        settingsCardApiV1MeSettingsCardsEventIdGet({
            path: { event_id: request.card_event_id },
            query: { organization_id: request.card_organization_id },
        }),
    );
}

export default function Privacy() {
    const { t, locale } = useI18n();
    const [view, setView] = useState<PrivacyOverview | null>(null);
    const [off, setOff] = useState(false);
    const [phrase, setPhrase] = useState('');
    const [card, setCard] = useState<SettingsCard | null>(null);
    const [busy, setBusy] = useState<string | null>(null);
    const [notice, setNotice] = useState<{ text: string; tone: 'bad' | 'live' | 'haldi' } | null>(null);
    const [contactsOn, setContactsOn] = useState(false);
    const answering = useRef(false);

    const load = useCallback(async () => {
        try {
            const overview = await call(myPrivacyApiV1MePrivacyGet());
            setView(overview);
            // A deletion already asked for waits on its card: show that card
            // here, whichever device asked.
            const waiting = overview.requests.find(
                (r) => r.kind === 'deletion' && r.status === 'awaiting_approval' && r.card_event_id && r.card_organization_id,
            );
            if (waiting) setCard(await cardFor(waiting));
        } catch (e) {
            if (e instanceof ApiError && e.status === 404) setOff(true);
            else setNotice({ text: e instanceof Error ? e.message : String(e), tone: 'bad' });
        }
        setContactsOn(await hasConsent(plain));
    }, []);

    useLoad(load, [load]);

    const run = async (key: string, work: () => Promise<void>) => {
        setBusy(key);
        setNotice(null);
        try {
            await work();
        } catch (e) {
            setNotice({ text: e instanceof Error ? e.message : String(e), tone: 'bad' });
        } finally {
            setBusy(null);
        }
    };

    const askDeletion = () =>
        run('erase', async () => {
            const request = await call(requestMyDeletionApiV1MePrivacyDeletionPost({ body: { phrase } }));
            // A request already waiting comes back without its card.
            setCard(request.card ?? (await cardFor(request)));
            await load();
        });

    const settle = (verb: 'confirm' | 'decline') =>
        run(verb, async () => {
            // One answer at a time from this screen; the server runs it once.
            if (!card || answering.current) return;
            answering.current = true;
            try {
                setCard(
                    await call(
                        settleSettingsCardApiV1MeSettingsCardsEventIdSettlePost({
                            path: { event_id: card.event_id },
                            body: { organization_id: card.organization_id, verb, version: card.version ?? null },
                        }),
                    ),
                );
            } finally {
                answering.current = false;
            }
        });

    return (
        <Screen edges={['bottom', 'left', 'right']} testID="screen-privacy">
            <Stack.Screen options={{ title: t('settings.privacy') }} />
            {off ? <Notice text={t('settings.notSwitchedOn')} /> : null}
            {!view && !off ? <Loading /> : null}
            {view ? (
                <>
                    <Section title={t('settings.privacy.export')}>
                        <Txt tone="ink2">{t('settings.privacy.exportHint')}</Txt>
                        <Button
                            label={t('settings.privacy.export')}
                            kind="secondary"
                            busy={busy === 'export'}
                            onPress={() =>
                                run('export', async () => {
                                    await call(requestMyExportApiV1MePrivacyExportPost());
                                    setNotice({ text: t('settings.privacy.exportRequested'), tone: 'live' });
                                    await load();
                                })
                            }
                            testID="privacy-export"
                        />
                    </Section>
                    {view.requests.length ? (
                        <Section title={t('settings.privacy.requests')}>
                            {view.requests.map((r, i) => (
                                <View key={r.id}>
                                    {i ? <Divider /> : null}
                                    <Row
                                        title={t('settings.privacy.status', { kind: r.kind, status: r.status })}
                                        subtitle={r.error ?? formatTime(r.created_at, locale)}
                                        right={
                                            r.kind === 'export' && r.status === 'ready' ? (
                                                <Button label={t('settings.privacy.download')} compact kind="secondary" onPress={() => run(`dl${r.id}`, () => downloadExport(r))} />
                                            ) : null
                                        }
                                    />
                                </View>
                            ))}
                        </Section>
                    ) : null}
                    <Section title={t('settings.privacy.erase')}>
                        {view.deletion_available ? (
                            <>
                                <Txt tone="ink2">{t('settings.privacy.eraseHint', { phrase: view.delete_phrase })}</Txt>
                                <Field label={t('settings.privacy.erasePhrase')} value={phrase} onChangeText={setPhrase} autoCapitalize="none" testID="privacy-phrase" />
                                <Button label={t('settings.privacy.eraseAsk')} kind="danger" disabled={phrase.trim() !== view.delete_phrase} busy={busy === 'erase'} onPress={askDeletion} testID="privacy-erase" />
                            </>
                        ) : (
                            <Notice text={t('settings.notSwitchedOn')} />
                        )}
                    </Section>
                    {card ? (
                        <Card testID="privacy-card">
                            <Txt size={13} weight="600" tone="ink2">
                                {t('card.wants')}
                            </Txt>
                            <Txt weight="700">{card.label}</Txt>
                            <Txt size={14}>{card.effect}</Txt>
                            {card.state === 'proposed' ? (
                                <View style={{ flexDirection: 'row', gap: 8 }}>
                                    <Button label={t('card.confirm')} kind="danger" style={{ flex: 1 }} busy={busy === 'confirm'} disabled={busy !== null} onPress={() => settle('confirm')} />
                                    <Button label={t('card.decline')} kind="secondary" style={{ flex: 1 }} disabled={busy !== null} onPress={() => settle('decline')} />
                                </View>
                            ) : (
                                <Txt weight="600">{card.state}</Txt>
                            )}
                        </Card>
                    ) : null}
                </>
            ) : null}
            {contactsOn ? (
                <Section title={t('people.title')}>
                    <Button
                        label={t('people.stop')}
                        kind="secondary"
                        onPress={() =>
                            run('contacts', async () => {
                                await stopSyncing({ people: createPeopleApi(plain), store: plain });
                                await cancelContactsSync();
                                setContactsOn(false);
                                setNotice({ text: t('people.stopped'), tone: 'live' });
                            })
                        }
                        testID="privacy-stop-contacts"
                    />
                </Section>
            ) : null}
            {notice ? <Notice text={notice.text} tone={notice.tone} /> : null}
        </Screen>
    );
}
