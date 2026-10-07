/**
 * Something shared into Decibyl from another app (the share sheet, through
 * expo-sharing): a link, text or a file becomes a new message in a new
 * chat. The person sees exactly what will be sent and can add a note; a
 * file is uploaded the same way the composer does (photos as a one-page
 * PDF). `decibyl://share?text=...` does the same for a link from elsewhere.
 */
import * as Sharing from 'expo-sharing';
import { useLocalSearchParams, useRouter } from 'expo-router';
import { useMemo, useState } from 'react';
import { Platform } from 'react-native';

import { postMessageApiV1TimelineMessagePost } from '@/client/sdk.gen';
import { Button, Card, Field, Loading, Notice, Screen, Title, Txt } from '@/components/ui';
import { call } from '@/lib/api';
import { refusal, uploadAttachment } from '@/lib/chat/attachments';
import type { Attachment } from '@/lib/chat/events';
import { newThreadId } from '@/lib/chat/ids';
import { jpegToPdf } from '@/lib/chat/pdf';
import { useI18n, type MessageKey } from '@/lib/i18n';
import { itemsFrom, type Item } from '@/lib/share';

function useShared() {
    // The hook exists on phones; the web build has nothing shared into it.
    if (Platform.OS === 'web') return { resolvedSharedPayloads: [], sharedPayloads: [], isResolving: false, clearSharedPayloads: () => undefined };
    // eslint-disable-next-line react-hooks/rules-of-hooks
    return Sharing.useIncomingShare();
}

async function upload(item: Item): Promise<Attachment> {
    const blob = await (await fetch(item.value)).blob();
    const isJpeg = (item.mime ?? '').includes('jpeg') || /\.jpe?g$/i.test(item.name ?? '');
    if (isJpeg) {
        const bytes = new Uint8Array(await blob.arrayBuffer());
        const pdf = jpegToPdf(bytes);
        const name = `${(item.name ?? 'photo').replace(/\.[^.]+$/, '')}.pdf`;
        return uploadAttachment({ name, body: pdf, size: pdf.length, mimeType: 'application/pdf' });
    }
    const name = item.name ?? 'shared-file';
    if (refusal({ name, size: item.size ?? blob.size })) throw new Error(name);
    return uploadAttachment({ name, body: blob, size: blob.size, mimeType: item.mime });
}

export default function Share() {
    const { t } = useI18n();
    const router = useRouter();
    const params = useLocalSearchParams<{ text?: string; url?: string }>();
    const shared = useShared();
    const items = useMemo(
        () => itemsFrom(shared.resolvedSharedPayloads.length ? shared.resolvedSharedPayloads : shared.sharedPayloads, params),
        [shared.resolvedSharedPayloads, shared.sharedPayloads, params],
    );
    const [note, setNote] = useState('');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const send = async () => {
        setBusy(true);
        setError(null);
        try {
            const attachments: Attachment[] = [];
            for (const item of items.filter((i) => i.kind === 'file')) {
                try {
                    attachments.push(await upload(item));
                } catch {
                    throw new Error(t('chat.attach.unsupported'));
                }
            }
            const words = items.filter((i) => i.kind !== 'file').map((i) => i.value);
            const text = [note.trim(), ...words].filter(Boolean).join('\n\n');
            const threadId = newThreadId();
            await call(postMessageApiV1TimelineMessagePost({ body: { assistant: true, thread_id: threadId, text, attachments } }));
            shared.clearSharedPayloads();
            router.replace(`/chat/${threadId}`);
        } catch (e) {
            setError(e instanceof Error ? e.message : String(e));
        } finally {
            setBusy(false);
        }
    };

    if (shared.isResolving) return <Loading />;
    return (
        <Screen edges={['bottom', 'left', 'right']} testID="screen-share">
            <Title>{t('share.title')}</Title>
            <Txt tone="ink2">{t('share.hint')}</Txt>
            {items.length ? (
                items.map((item, i) => (
                    <Card key={i}>
                        <Txt size={12} weight="600" tone="ink2">
                            {t(`share.${item.kind}` as MessageKey)}
                        </Txt>
                        <Txt selectable numberOfLines={6}>
                            {item.kind === 'file' ? item.name : item.value}
                        </Txt>
                    </Card>
                ))
            ) : (
                <Notice text={t('share.nothing')} />
            )}
            <Field label={t('share.addNote')} value={note} onChangeText={setNote} multiline testID="share-note" />
            {error ? <Notice text={error} tone="bad" /> : null}
            <Button label={t('share.send')} onPress={send} busy={busy} disabled={!items.length && !note.trim()} testID="share-send" />
        </Screen>
    );
}
