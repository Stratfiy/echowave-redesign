/**
 * Today (screen 07): one ordered list -- what waits for the person's OK
 * (docked first), what is due, the brief, what is coming up, a few
 * suggestions and the end-of-day note -- in the order the server gives
 * (`order`). Each section says when it could not be loaded; a failure is
 * never shown as "nothing today".
 */
import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useState } from 'react';
import { RefreshControl, ScrollView, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import {
    dismissSuggestionApiV1TodaySuggestionsKeyDismissPost,
    getTodayApiV1TodayGet,
    proposeCallBackApiV1TodayMissedCallsMissedCallIdCallBackPost,
    refreshBriefApiV1TodayBriefRefreshPost,
    setReminderStatusApiV1TodayRemindersReminderIdStatusPost,
    snoozeReminderApiV1TodayRemindersReminderIdSnoozePost,
} from '@/client/sdk.gen';
import { Button, Card, Divider, Empty, Failed, Loading, Notice, Row, Section, Txt } from '@/components/ui';
import { ApiError, call } from '@/lib/api';
import { settler } from '@/lib/approvals/settler';
import { useI18n } from '@/lib/i18n';
import { routeForPath } from '@/lib/links';
import { useTheme } from '@/lib/theme';
import type { ApprovalItem, BriefView, DueItem, TodayView } from '@/lib/today/types';

export default function Today() {
    const { t } = useI18n();
    const theme = useTheme();
    const router = useRouter();
    const [today, setToday] = useState<TodayView | null>(null);
    const [off, setOff] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [refreshing, setRefreshing] = useState(false);
    const [note, setNote] = useState<string | null>(null);

    const load = useCallback(async () => {
        try {
            setToday((await call(getTodayApiV1TodayGet())) as unknown as TodayView);
            setOff(false);
            setError(null);
        } catch (e) {
            if (e instanceof ApiError && e.status === 404) setOff(true);
            else setError(e instanceof Error ? e.message : String(e));
        }
    }, []);

    useFocusEffect(
        useCallback(() => {
            void load();
        }, [load]),
    );

    const act = async (work: () => Promise<unknown>) => {
        setNote(null);
        try {
            await work();
        } catch (e) {
            setNote(e instanceof Error ? e.message : String(e));
        }
        await load();
    };

    const approval = (item: ApprovalItem) => (
        <Card key={item.id} testID={`today-approval-${item.id}`}>
            <Txt weight="700">{item.sentence || `${t('card.wants')} ${item.label}`}</Txt>
            {item.detail ? <Txt size={14}>{item.detail}</Txt> : null}
            <View style={{ flexDirection: 'row', gap: 8 }}>
                <Button label={t('card.confirm')} compact style={{ flex: 1 }} onPress={() => act(() => settler.answer(item.id, 'confirm', item.version))} testID={`today-approval-${item.id}-confirm`} />
                <Button label={t('card.decline')} kind="secondary" compact style={{ flex: 1 }} onPress={() => act(() => settler.answer(item.id, 'decline'))} />
                <Button label={t('card.open')} kind="quiet" compact onPress={() => router.push(`/approvals/${item.id}`)} />
            </View>
        </Card>
    );

    const due = (item: DueItem) => {
        if (item.kind === 'reminder') {
            return (
                <View key={`r${item.id}`} style={{ gap: 6 }}>
                    <Row
                        title={item.title}
                        subtitle={[item.overdue ? t('today.overdue') : null, item.when].filter(Boolean).join(' · ')}
                        onPress={() => router.push(`/reminders/${item.id}`)}
                        chevron
                        testID={`today-reminder-${item.id}`}
                    />
                    <View style={{ flexDirection: 'row', gap: 8 }}>
                        <Button label={t('today.reminder.done')} compact kind="secondary" onPress={() => act(() => call(setReminderStatusApiV1TodayRemindersReminderIdStatusPost({ path: { reminder_id: item.id }, body: { verb: 'complete' } })))} />
                        <Button label={t('today.reminder.snooze')} compact kind="quiet" onPress={() => act(() => call(snoozeReminderApiV1TodayRemindersReminderIdSnoozePost({ path: { reminder_id: item.id }, body: { minutes: 60 } })))} />
                    </View>
                </View>
            );
        }
        if (item.kind === 'missed_call') {
            return (
                <View key={`m${item.id}`} style={{ gap: 6 }}>
                    <Row title={item.title} subtitle={[t('today.missedCall'), item.when].filter(Boolean).join(' · ')} />
                    <Button label={t('today.callBack')} compact kind="secondary" onPress={() => act(() => call(proposeCallBackApiV1TodayMissedCallsMissedCallIdCallBackPost({ path: { missed_call_id: item.id } })))} />
                </View>
            );
        }
        return (
            <Row
                key={`t${item.id}`}
                title={item.title}
                subtitle={[item.needs_input ? t('task.needs_input') : null, item.when].filter(Boolean).join(' · ')}
                onPress={item.href ? () => router.push(routeForPath(item.href as string) as never) : undefined}
                chevron={Boolean(item.href)}
            />
        );
    };

    const brief = (view: BriefView | null, refresh?: () => void) =>
        view ? (
            <Card>
                <Txt selectable>{view.summary}</Txt>
                {view.sources?.length ? (
                    <Txt size={12} tone="ink3">
                        {view.sources.map((s) => `${s.label}: ${s.status}`).join(' · ')}
                    </Txt>
                ) : null}
                {refresh ? <Button label={t('today.brief.refresh')} kind="quiet" compact onPress={refresh} /> : null}
            </Card>
        ) : (
            <Txt tone="ink2">{t('today.brief.none')}</Txt>
        );

    const sections = today?.sections;
    const failed = (s: { state: string } | null | undefined) => s?.state === 'failed';
    const render: Record<string, () => React.ReactNode> = {
        approvals: () =>
            sections?.approvals && (failed(sections.approvals) || sections.approvals.items?.length) ? (
                <Section key="approvals" title={t('today.approvals')} testID="today-approvals">
                    {failed(sections.approvals) ? <Notice text={t('today.sectionFailed')} tone="bad" /> : sections.approvals.items?.map(approval)}
                </Section>
            ) : null,
        due: () =>
            sections?.due && (failed(sections.due) || sections.due.items.length || sections.due.active?.length) ? (
                <Section key="due" title={t('today.due')} testID="today-due">
                    {failed(sections.due) ? <Notice text={t('today.sectionFailed')} tone="bad" /> : null}
                    {sections.due.active?.map((job) => <Row key={`a${job.id}`} title={job.title} subtitle={t(`task.${job.state}` as never)} />)}
                    {sections.due.items.map(due)}
                </Section>
            ) : null,
        brief: () =>
            sections?.brief && sections.brief.enabled ? (
                <Section key="brief" title={t('today.brief')} testID="today-brief">
                    {failed(sections.brief) ? <Notice text={t('today.sectionFailed')} tone="bad" /> : brief(sections.brief.item, () => act(() => call(refreshBriefApiV1TodayBriefRefreshPost())))}
                    {sections.brief.next_sentence ? <Txt size={13} tone="ink2">{sections.brief.next_sentence}</Txt> : null}
                </Section>
            ) : null,
        upcoming: () =>
            sections?.upcoming && (failed(sections.upcoming) || sections.upcoming.items.length) ? (
                <Section key="upcoming" title={t('today.upcoming')} testID="today-upcoming">
                    {failed(sections.upcoming) ? <Notice text={t('today.sectionFailed')} tone="bad" /> : sections.upcoming.items.map((e) => <Row key={`e${e.id}`} title={e.title} subtitle={e.when} />)}
                </Section>
            ) : null,
        suggestions: () =>
            sections?.suggestions?.items?.length ? (
                <Section key="suggestions" title={t('today.suggestions')}>
                    {sections.suggestions.items.map((s) => (
                        <Card key={s.key}>
                            <Txt weight="600">{s.title}</Txt>
                            {s.why ? <Txt size={13} tone="ink2">{s.why}</Txt> : null}
                            <View style={{ flexDirection: 'row', gap: 8 }}>
                                {s.action.kind === 'propose_callback' ? (
                                    <Button label={t('today.callBack')} compact onPress={() => act(() => call(proposeCallBackApiV1TodayMissedCallsMissedCallIdCallBackPost({ path: { missed_call_id: (s.action as { missed_call_id: number }).missed_call_id } })))} />
                                ) : null}
                                {s.action.kind === 'open_reminder' ? (
                                    <Button label={t('card.open')} compact onPress={() => router.push(`/reminders/${(s.action as { reminder_id: number }).reminder_id}`)} />
                                ) : null}
                                <Button label={t('today.dismiss')} kind="quiet" compact onPress={() => act(() => call(dismissSuggestionApiV1TodaySuggestionsKeyDismissPost({ path: { key: s.key }, body: {} })))} />
                            </View>
                        </Card>
                    ))}
                </Section>
            ) : null,
        end_of_day: () =>
            sections?.end_of_day?.item ? (
                <Section key="end_of_day" title={t('today.endOfDay')}>
                    {brief(sections.end_of_day.item)}
                </Section>
            ) : null,
    };

    return (
        <SafeAreaView edges={['left', 'right']} style={{ flex: 1, backgroundColor: theme.colors.paper }} testID="screen-today">
            <ScrollView
                contentContainerStyle={{ padding: 16, gap: 18 }}
                refreshControl={
                    <RefreshControl
                        refreshing={refreshing}
                        onRefresh={async () => {
                            setRefreshing(true);
                            await load();
                            setRefreshing(false);
                        }}
                    />
                }
            >
                {today ? (
                    <Txt size={15} weight="600" tone="ink2">
                        {today.date_label}
                    </Txt>
                ) : null}
                {off ? <Notice text={t('today.off')} /> : null}
                {error ? <Failed text={error} onRetry={load} retryLabel={t('common.retry')} /> : null}
                {note ? <Notice text={note} tone="bad" /> : null}
                {!today && !off && !error ? <Loading label={t('common.loading')} /> : null}
                {today?.missing_sources?.map((m) => <Notice key={m.kind} text={m.message} tone="haldi" />)}
                {today ? (today.order ?? Object.keys(render)).map((key) => render[key]?.() ?? null) : null}
                {today?.empty ? <Empty text={today.empty_copy || t('today.empty')} /> : null}
                <Divider />
                <Button label={t('today.newReminder')} kind="secondary" onPress={() => router.push('/reminders/new')} testID="today-new-reminder" />
            </ScrollView>
        </SafeAreaView>
    );
}
