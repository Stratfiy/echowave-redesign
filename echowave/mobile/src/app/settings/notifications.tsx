/**
 * Notifications (screen 21) on the phone: push to this phone, what to be
 * told about, quiet hours and lock-screen privacy -- the person's own
 * preferences (GET/PUT /me/notifications, flag `identity_notifications`),
 * the same ones the web edits. "Push to this phone" asks the phone for
 * permission only when switched on, registers the Expo token
 * (flag `mobile_push`), then turns the push channel on.
 */
import { useLoad } from '@/lib/useLoad';
import { Stack } from 'expo-router';
import { useCallback, useState } from 'react';
import { Linking, View } from 'react-native';

import {
    listMobileDevicesApiV1MeMobilePushDevicesGet,
    myNotificationsApiV1MeNotificationsGet,
    removeMobileDeviceApiV1MeMobilePushDevicesDeviceIdDelete,
    saveNotificationsApiV1MeNotificationsPut,
    testNotificationApiV1MeNotificationsTestPost,
} from '@/client/sdk.gen';
import type { MobileDevice, NotificationsChange, NotificationsView } from '@/client/types.gen';
import { Button, Divider, Field, Loading, Notice, Row, Screen, Section, Toggle, Txt } from '@/components/ui';
import { ApiError, call } from '@/lib/api';
import { useFeature } from '@/lib/features';
import { useI18n } from '@/lib/i18n';
import { registerForPush, type PushResult } from '@/lib/push';
import { phonePushDeps } from '@/lib/pushDevice';

export default function Notifications() {
    const { t } = useI18n();
    const mobilePush = useFeature('mobile_push');
    const [view, setView] = useState<NotificationsView | null>(null);
    const [off, setOff] = useState(false);
    const [phones, setPhones] = useState<MobileDevice[]>([]);
    const [quiet, setQuiet] = useState({ start: '', end: '' });
    const [push, setPush] = useState<PushResult | null>(null);
    const [notice, setNotice] = useState<{ text: string; tone: 'bad' | 'live' | 'haldi' } | null>(null);

    const load = useCallback(async () => {
        try {
            const v = await call(myNotificationsApiV1MeNotificationsGet());
            setView(v);
            setQuiet({ start: v.quiet_start ?? '', end: v.quiet_end ?? '' });
        } catch (e) {
            if (e instanceof ApiError && e.status === 404) setOff(true);
            else setNotice({ text: e instanceof Error ? e.message : String(e), tone: 'bad' });
        }
        if (mobilePush) {
            try {
                setPhones((await call(listMobileDevicesApiV1MeMobilePushDevicesGet())).devices);
            } catch {
                setPhones([]);
            }
        }
    }, [mobilePush]);

    useLoad(load, [load]);

    /** Save naming the revision read; on a conflict, once more on the stored one. */
    const save = async (change: Omit<NotificationsChange, 'revision'>) => {
        if (!view) return;
        setNotice(null);
        const put = (revision: number) => call(saveNotificationsApiV1MeNotificationsPut({ body: { ...change, revision } }));
        try {
            setView(await put(view.revision));
        } catch (e) {
            const stored = e instanceof ApiError && e.status === 409 ? (e.detail as { stored_revision?: number })?.stored_revision : undefined;
            if (typeof stored === 'number') {
                setView(await put(stored));
                setNotice({ text: t('common.changedElsewhere'), tone: 'haldi' });
            } else setNotice({ text: e instanceof Error ? e.message : String(e), tone: 'bad' });
        }
    };

    const togglePhone = async (on: boolean) => {
        if (!on) {
            await save({ channels: { push: false } });
            return;
        }
        const result = await registerForPush(phonePushDeps(), { ask: true });
        setPush(result);
        if (result.status === 'registered') {
            await save({ channels: { push: true } });
            await load();
        }
    };

    if (off) {
        return (
            <Screen edges={['bottom', 'left', 'right']} testID="screen-notifications">
                <Stack.Screen options={{ title: t('settings.notifications') }} />
                <Notice text={t('settings.notSwitchedOn')} />
            </Screen>
        );
    }
    if (!view) return <Loading />;

    const pushReady = view.availability.push?.available;
    return (
        <Screen edges={['bottom', 'left', 'right']} testID="screen-notifications">
            <Stack.Screen options={{ title: t('settings.notifications') }} />
            <Section>
                <Toggle
                    label={t('settings.notif.pushHere')}
                    hint={pushReady ? t('settings.notif.pushHint') : view.availability.push?.reason ?? undefined}
                    value={Boolean(view.channels.push)}
                    onChange={togglePhone}
                    disabled={!pushReady}
                    testID="notif-push"
                />
                {push?.status === 'denied' ? (
                    <>
                        <Notice text={t('settings.notif.denied')} tone="bad" />
                        <Button label={t('people.openSettings')} kind="secondary" compact onPress={() => Linking.openSettings()} />
                    </>
                ) : null}
                {push && (push.status === 'unsupported' || push.status === 'failed' || push.status === 'off') ? (
                    <Notice text={push.status === 'failed' ? push.message : push.status === 'off' ? t('settings.notSwitchedOn') : t('settings.notif.unsupported')} tone="haldi" />
                ) : null}
            </Section>
            <Section title={t('settings.notif.topics')} testID="notif-topics">
                {view.topic_list.map((topic, i) => (
                    <View key={topic.name}>
                        {i ? <Divider /> : null}
                        <Toggle
                            label={topic.label}
                            value={Boolean(view.topics[topic.name]?.on)}
                            onChange={(on) => save({ topics: { [topic.name]: { on } } })}
                            testID={`notif-topic-${topic.name}`}
                        />
                    </View>
                ))}
            </Section>
            <Section title={t('settings.notif.quiet')}>
                <View style={{ flexDirection: 'row', gap: 12 }}>
                    <View style={{ flex: 1 }}>
                        <Field label={t('settings.notif.from')} placeholder="22:00" value={quiet.start} onChangeText={(start) => setQuiet((q) => ({ ...q, start }))} />
                    </View>
                    <View style={{ flex: 1 }}>
                        <Field label={t('settings.notif.to')} placeholder="07:00" value={quiet.end} onChangeText={(end) => setQuiet((q) => ({ ...q, end }))} />
                    </View>
                </View>
                <Button
                    label={t('common.save')}
                    kind="secondary"
                    compact
                    onPress={() => save({ quiet_start: quiet.start || null, quiet_end: quiet.end || null })}
                />
                <Txt size={13} tone="ink2">{t('settings.notif.quietHint')}</Txt>
            </Section>
            <Toggle label={t('settings.notif.private')} value={view.private_previews} onChange={(on) => save({ private_previews: on })} testID="notif-private" />
            {phones.length ? (
                <Section title={t('settings.notif.devices')}>
                    {phones.map((phone) => (
                        <Row
                            key={phone.id}
                            title={phone.label}
                            subtitle={`${phone.platform} · ${phone.state}`}
                            right={
                                phone.state !== 'revoked' ? (
                                    <Button
                                        label="✕"
                                        kind="quiet"
                                        compact
                                        onPress={async () => setPhones((await call(removeMobileDeviceApiV1MeMobilePushDevicesDeviceIdDelete({ path: { device_id: phone.id } }))).devices)}
                                    />
                                ) : null
                            }
                        />
                    ))}
                </Section>
            ) : null}
            <Button
                label={t('settings.notif.test')}
                kind="secondary"
                onPress={async () => {
                    const result = await call(testNotificationApiV1MeNotificationsTestPost()).catch(() => null);
                    setNotice({ text: t('settings.notif.testSent', { outcome: result?.push ?? '—' }), tone: 'live' });
                }}
            />
            {notice ? <Notice text={notice.text} tone={notice.tone} /> : null}
        </Screen>
    );
}
