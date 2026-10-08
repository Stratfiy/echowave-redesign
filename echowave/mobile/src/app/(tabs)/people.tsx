/**
 * People: the person's contacts, each with a brief and their last
 * interactions with Decibyl.
 *
 * Consent first, in plain words, before the phone's own Contacts prompt:
 * what is read (names, numbers, emails), where it goes, that it is private
 * to them, and how to stop. Nothing is read before "Agree and sync", and
 * nothing leaves the phone before it. Re-syncs are incremental, on open and
 * in the background.
 */
import { Ionicons } from '@expo/vector-icons';
import { useFocusEffect, useRouter } from 'expo-router';
import { useCallback, useMemo, useState } from 'react';
import { FlatList, Linking, Pressable, TextInput, View } from 'react-native';
import { SafeAreaView } from 'react-native-safe-area-context';

import { Button, Card, Divider, Empty, Notice, Txt } from '@/components/ui';
import { scheduleContactsSync } from '@/lib/background/tasks';
import { giveConsent, hasConsent } from '@/lib/contacts/consent';
import { contactSource, isDemoContacts } from '@/lib/contacts/source';
import { lastSyncAt, syncContacts, type SyncResult } from '@/lib/contacts/sync';
import { formatTime, useI18n } from '@/lib/i18n';
import { createPeopleApi, type Person } from '@/lib/people/api';
import { displayPhone } from '@/lib/phone';
import { plain } from '@/lib/storage';
import { useTheme } from '@/lib/theme';

export default function People() {
    const { t, locale } = useI18n();
    const theme = useTheme();
    const router = useRouter();
    const people = useMemo(() => createPeopleApi(plain), []);
    const source = useMemo(() => contactSource(), []);
    const [consent, setConsent] = useState<boolean | null>(null);
    const [list, setList] = useState<Person[]>([]);
    const [search, setSearch] = useState('');
    const [syncing, setSyncing] = useState(false);
    const [result, setResult] = useState<SyncResult | null>(null);
    const [lastAt, setLastAt] = useState<string | null>(null);

    const refresh = useCallback(
        async (query = search) => {
            setConsent(await hasConsent(plain));
            setList(await people.list(query));
            setLastAt(await lastSyncAt(plain));
        },
        [people, search],
    );

    const sync = useCallback(async () => {
        setSyncing(true);
        const outcome = await syncContacts({ source, people, store: plain });
        setResult(outcome);
        setSyncing(false);
        await refresh();
    }, [source, people, refresh]);

    useFocusEffect(
        useCallback(() => {
            void refresh();
        }, [refresh]),
    );

    const agree = async () => {
        const permission = await source.request();
        if (permission !== 'granted') {
            setResult({ status: 'no_permission', permission });
            return;
        }
        await giveConsent(plain);
        setConsent(true);
        await scheduleContactsSync();
        await sync();
    };

    if (!source.available()) {
        return (
            <SafeAreaView edges={['left', 'right']} style={{ flex: 1, backgroundColor: theme.colors.paper, padding: 16 }} testID="screen-people">
                <Notice text={t('people.unavailableWeb')} />
            </SafeAreaView>
        );
    }

    if (consent === false) {
        return (
            <SafeAreaView edges={['left', 'right']} style={{ flex: 1, backgroundColor: theme.colors.paper }} testID="screen-people-consent">
                <View style={{ padding: 16, gap: 14 }}>
                    <Ionicons name="people-circle-outline" size={56} color={theme.colors.ink2} />
                    <Txt size={22} weight="700">
                        {t('people.consent.title')}
                    </Txt>
                    <Txt>{t('people.consent.body')}</Txt>
                    <Card>
                        <Txt size={14}>{people.kind === 'device' ? t('people.consent.whereDevice') : t('people.consent.where')}</Txt>
                    </Card>
                    {result?.status === 'no_permission' ? (
                        <>
                            <Notice text={t('people.permissionDenied')} tone="bad" />
                            <Button label={t('people.openSettings')} kind="secondary" onPress={() => Linking.openSettings()} />
                        </>
                    ) : null}
                    <Button label={t('people.consent.agree')} onPress={agree} testID="people-agree" />
                    <Button label={t('people.consent.decline')} kind="quiet" onPress={() => router.navigate('/')} />
                </View>
            </SafeAreaView>
        );
    }

    const summary =
        result?.status === 'synced'
            ? t('people.syncSummary', { added: result.added, changed: result.changed, removed: result.removed })
            : result?.status === 'failed'
              ? result.message
              : null;

    return (
        <SafeAreaView edges={['left', 'right']} style={{ flex: 1, backgroundColor: theme.colors.paper }} testID="screen-people">
            <FlatList
                data={list}
                keyExtractor={(p) => p.id}
                ItemSeparatorComponent={Divider}
                ListHeaderComponent={
                    <View style={{ padding: 16, gap: 10 }}>
                        <TextInput
                            accessibilityLabel={t('people.search')}
                            placeholder={t('people.search')}
                            placeholderTextColor={theme.colors.ink3}
                            value={search}
                            onChangeText={(v) => {
                                setSearch(v);
                                void refresh(v);
                            }}
                            style={{ minHeight: theme.control, borderWidth: 1, borderColor: theme.colors.line, borderRadius: 22, paddingHorizontal: 14, color: theme.colors.ink, fontSize: 16 * theme.scale, backgroundColor: theme.colors.paper2 }}
                            testID="people-search"
                        />
                        {people.kind === 'device' ? <Notice text={t('people.serverPending')} /> : null}
                        {isDemoContacts() ? <Notice text="Development walkthrough: sample contacts, not an address book." tone="haldi" /> : null}
                        <View style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                            <Txt size={13} tone="ink2" style={{ flex: 1 }}>
                                {syncing ? t('people.syncing') : [lastAt ? t('people.syncedAt', { when: formatTime(lastAt, locale) }) : null, summary].filter(Boolean).join(' · ')}
                            </Txt>
                            <Button label={t('people.syncNow')} kind="secondary" compact onPress={sync} busy={syncing} testID="people-sync" />
                        </View>
                        {result?.status === 'no_permission' ? <Notice text={t('people.permissionDenied')} tone="bad" /> : null}
                    </View>
                }
                ListEmptyComponent={<Empty text={t('people.empty')} />}
                renderItem={({ item }) => (
                    <Pressable
                        accessibilityRole="button"
                        accessibilityLabel={item.name}
                        onPress={() => router.push(`/person/${encodeURIComponent(item.id)}`)}
                        style={({ pressed }) => ({ paddingHorizontal: 16, paddingVertical: 10, flexDirection: 'row', alignItems: 'center', gap: 12, opacity: pressed ? 0.6 : 1, minHeight: theme.control })}
                        testID={`person-${item.id}`}
                    >
                        <View style={{ width: 40, height: 40, borderRadius: 20, backgroundColor: theme.colors.paper2, alignItems: 'center', justifyContent: 'center' }}>
                            <Txt weight="700">{item.name.slice(0, 1).toUpperCase()}</Txt>
                        </View>
                        <View style={{ flex: 1 }}>
                            <Txt weight="600" numberOfLines={1}>
                                {item.name}
                            </Txt>
                            <Txt size={13} tone="ink2" numberOfLines={1}>
                                {item.phones[0] ? displayPhone(item.phones[0]) : item.emails[0] ?? ''}
                            </Txt>
                        </View>
                        <Txt tone="ink3">›</Txt>
                    </Pressable>
                )}
            />
        </SafeAreaView>
    );
}

