/** One person: how to reach them, Decibyl's brief, the last interactions,
 * and "Ask Decibyl about them" -- a new chat with the question in the box. */
import { Stack, useLocalSearchParams, useRouter } from 'expo-router';
import { useEffect, useMemo, useState } from 'react';

import { Button, Card, Loading, Notice, Row, Screen, Section, Title, Txt } from '@/components/ui';
import { formatTime, useI18n } from '@/lib/i18n';
import { createPeopleApi, type PersonDetail } from '@/lib/people/api';
import { displayPhone } from '@/lib/phone';
import { plain } from '@/lib/storage';

export default function PersonScreen() {
    const { id } = useLocalSearchParams<{ id: string }>();
    const { t, locale } = useI18n();
    const router = useRouter();
    const people = useMemo(() => createPeopleApi(plain), []);
    const [person, setPerson] = useState<PersonDetail | null | undefined>(undefined);

    useEffect(() => {
        void people.get(decodeURIComponent(String(id))).then(setPerson);
    }, [people, id]);

    if (person === undefined) return <Loading />;
    if (person === null) return <Screen><Notice text={t('people.empty')} /></Screen>;

    return (
        <Screen edges={['bottom', 'left', 'right']} testID="screen-person">
            <Stack.Screen options={{ title: person.name }} />
            <Title>{person.name}</Title>
            {person.phones.length ? (
                <Section title={t('people.phones')}>
                    {person.phones.map((p) => <Row key={p} title={displayPhone(p)} />)}
                </Section>
            ) : null}
            {person.emails.length ? (
                <Section title={t('people.emails')}>
                    {person.emails.map((e) => <Row key={e} title={e} />)}
                </Section>
            ) : null}
            <Section title={t('people.brief')}>
                <Card>
                    <Txt tone={person.brief ? 'ink' : 'ink2'}>{person.brief ?? t('people.noBrief')}</Txt>
                </Card>
            </Section>
            <Section title={t('people.interactions')}>
                {person.interactions.length ? (
                    person.interactions.map((i, n) => (
                        <Row
                            key={n}
                            title={i.summary}
                            subtitle={`${i.kind} · ${formatTime(i.at, locale)}`}
                            onPress={i.thread_id ? () => router.push(`/chat/${i.thread_id}`) : undefined}
                            chevron={Boolean(i.thread_id)}
                        />
                    ))
                ) : (
                    <Txt tone="ink2">{t('people.noInteractions')}</Txt>
                )}
            </Section>
            {people.kind === 'device' ? <Notice text={t('people.serverPending')} /> : null}
            <Button
                label={t('people.ask', { name: person.name })}
                onPress={() => router.push(`/chat/new?q=${encodeURIComponent(t('people.askPrompt', { name: person.name }))}`)}
                testID="person-ask"
            />
        </Screen>
    );
}
