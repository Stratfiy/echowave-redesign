/**
 * A reminder (screen 10): a new one is the words of what, a day and a time,
 * checked by the server (`/today/reminders/preview` says the next time in a
 * sentence and any problem) before it is set; an existing one can be marked
 * done, paused, resumed, snoozed or cancelled.
 */
import { useLoad } from '@/lib/useLoad';
import { Stack, useLocalSearchParams, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import { View } from 'react-native';

import {
    createReminderApiV1TodayRemindersPost,
    getReminderApiV1TodayRemindersReminderIdGet,
    previewReminderApiV1TodayRemindersPreviewPost,
    resolveDateApiV1TodayResolveDatePost,
    setReminderStatusApiV1TodayRemindersReminderIdStatusPost,
    snoozeReminderApiV1TodayRemindersReminderIdSnoozePost,
} from '@/client/sdk.gen';
import type { ReminderDraft } from '@/client/types.gen';
import { Button, Card, Chip, Failed, Field, Loading, Notice, Screen, Section, Title, Txt } from '@/components/ui';
import { call } from '@/lib/api';
import { useI18n, type MessageKey } from '@/lib/i18n';
import type { ReminderPreview, ReminderView } from '@/lib/today/types';

export default function Reminder() {
    const { id } = useLocalSearchParams<{ id: string }>();
    return id === 'new' ? <NewReminder /> : <ExistingReminder id={Number(id)} />;
}

function NewReminder() {
    const { t } = useI18n();
    const router = useRouter();
    const [title, setTitle] = useState('');
    const [day, setDay] = useState<'today' | 'tomorrow'>('tomorrow');
    const [time, setTime] = useState('09:00');
    const [recurrence, setRecurrence] = useState<'once' | 'daily' | 'weekdays'>('once');
    const [channel, setChannel] = useState<'in_app' | 'push'>('in_app');
    const [preview, setPreview] = useState<ReminderPreview | null>(null);
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const draft = async (): Promise<ReminderDraft> => {
        const resolved = (await call(resolveDateApiV1TodayResolveDatePost({ body: { words: day, local_time: time } }))) as { date: string };
        return { title: title.trim(), recurrence, date: resolved.date, local_time: time, channel };
    };

    const check = async () => {
        setBusy(true);
        setError(null);
        try {
            setPreview((await call(previewReminderApiV1TodayRemindersPreviewPost({ body: await draft() }))) as unknown as ReminderPreview);
        } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
        } finally {
            setBusy(false);
        }
    };

    const save = async () => {
        if (!preview) return;
        setBusy(true);
        setError(null);
        try {
            const created = (await call(
                createReminderApiV1TodayRemindersPost({ body: { ...(await draft()), schedule_key: preview.schedule_key } }),
            )) as unknown as ReminderView;
            router.replace(`/reminders/${created.id}`);
        } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
        } finally {
            setBusy(false);
        }
    };

    return (
        <Screen edges={['bottom', 'left', 'right']} testID="screen-reminder-new">
            <Stack.Screen options={{ title: t('reminder.new') }} />
            <Field label={t('reminder.what')} value={title} onChangeText={(v) => { setTitle(v); setPreview(null); }} testID="reminder-title" />
            <Section title={t('reminder.day')}>
                <View style={{ flexDirection: 'row', gap: 8 }}>
                    <Chip label={t('reminder.today')} selected={day === 'today'} onPress={() => { setDay('today'); setPreview(null); }} />
                    <Chip label={t('reminder.tomorrow')} selected={day === 'tomorrow'} onPress={() => { setDay('tomorrow'); setPreview(null); }} />
                </View>
            </Section>
            <Field label={t('reminder.time')} value={time} onChangeText={(v) => { setTime(v); setPreview(null); }} keyboardType="numbers-and-punctuation" testID="reminder-time" />
            <Section title={t('reminder.repeat')}>
                <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap' }}>
                    {(['once', 'daily', 'weekdays'] as const).map((r) => (
                        <Chip key={r} label={t(`reminder.${r}` as MessageKey)} selected={recurrence === r} onPress={() => { setRecurrence(r); setPreview(null); }} />
                    ))}
                </View>
            </Section>
            <Section title={t('reminder.channel')}>
                <View style={{ flexDirection: 'row', gap: 8 }}>
                    {(['in_app', 'push'] as const).map((c) => (
                        <Chip key={c} label={t(`reminder.channel.${c}` as MessageKey)} selected={channel === c} onPress={() => { setChannel(c); setPreview(null); }} />
                    ))}
                </View>
            </Section>
            {preview ? (
                <Card>
                    <Txt weight="600">{preview.sentence}</Txt>
                    {preview.problems.map((p) => <Notice key={p.code} text={p.message} tone="bad" />)}
                </Card>
            ) : null}
            {error ? <Notice text={error} tone="bad" /> : null}
            {preview && !preview.problems.length ? (
                <Button label={t('reminder.create')} onPress={save} busy={busy} testID="reminder-create" />
            ) : (
                <Button label={t('reminder.preview')} onPress={check} busy={busy} disabled={!title.trim()} testID="reminder-check" />
            )}
        </Screen>
    );
}

function ExistingReminder({ id }: { id: number }) {
    const { t } = useI18n();
    const [view, setView] = useState<ReminderView | null>(null);
    const [error, setError] = useState<string | null>(null);

    const load = useCallback(async () => {
        try {
            setView((await call(getReminderApiV1TodayRemindersReminderIdGet({ path: { reminder_id: id } }))) as unknown as ReminderView);
            setError(null);
        } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
        }
    }, [id]);

    useLoad(load, [load]);

    const act = async (work: () => Promise<unknown>) => {
        try {
            await work();
        } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
        }
        await load();
    };
    const status = (verb: 'complete' | 'pause' | 'resume' | 'cancel') =>
        act(() => call(setReminderStatusApiV1TodayRemindersReminderIdStatusPost({ path: { reminder_id: id }, body: { verb } })));

    return (
        <Screen edges={['bottom', 'left', 'right']} testID="screen-reminder">
            <Stack.Screen options={{ title: t('reminder.title') }} />
            {error ? <Failed text={error} onRetry={load} retryLabel={t('common.retry')} /> : null}
            {!view && !error ? <Loading /> : null}
            {view ? (
                <>
                    <Title>{view.title}</Title>
                    {view.note ? <Txt tone="ink2">{view.note}</Txt> : null}
                    <Card>
                        <Txt weight="600">{t(`reminder.status.${view.status}` as MessageKey)}</Txt>
                        {view.when ? <Txt>{t('reminder.next', { when: view.when })}</Txt> : null}
                        {view.offset_words ? <Txt size={13} tone="ink2">{view.offset_words}</Txt> : null}
                    </Card>
                    {view.status === 'active' ? (
                        <>
                            <Button label={t('reminder.complete')} onPress={() => status('complete')} testID="reminder-complete" />
                            <Button label={t('today.reminder.snooze')} kind="secondary" onPress={() => act(() => call(snoozeReminderApiV1TodayRemindersReminderIdSnoozePost({ path: { reminder_id: id }, body: { minutes: 60 } })))} />
                            <Button label={t('reminder.pause')} kind="secondary" onPress={() => status('pause')} />
                        </>
                    ) : null}
                    {view.status === 'paused' ? <Button label={t('reminder.resume')} onPress={() => status('resume')} /> : null}
                    {view.status === 'active' || view.status === 'paused' ? <Button label={t('reminder.cancel')} kind="quiet" onPress={() => status('cancel')} /> : null}
                </>
            ) : null}
        </Screen>
    );
}
