/**
 * The box at the bottom of a thread: type, attach (camera, photos, files),
 * record a voice note, send -- or Stop while Decibyl is working.
 *
 * Attachments upload as soon as they are picked (src/lib/chat/attachments.ts)
 * so a slow upload shows while the person is still typing. A photo is sent
 * as a one-page PDF so the knowledge base can read the words in it. A voice
 * note is transcribed and folded into the message as text, like the web
 * ("Voice note (0:42), transcribed: ..."): the words can be checked before
 * sending, and no audio is kept.
 *
 * Talk (live voice) is a different thing and lives in the header.
 */
import { Ionicons } from '@expo/vector-icons';
import { AudioModule, RecordingPresets, setAudioModeAsync, useAudioRecorder, useAudioRecorderState } from 'expo-audio';
import * as DocumentPicker from 'expo-document-picker';
import * as ImagePicker from 'expo-image-picker';
import { useEffect, useState } from 'react';
import { ActionSheetIOS, Platform, Pressable, TextInput, View } from 'react-native';

import { Button, Txt } from '@/components/ui';
import { durationLabel, refusal, uploadAttachment } from '@/lib/chat/attachments';
import type { Attachment } from '@/lib/chat/events';
import { base64ToBytes, jpegToPdf } from '@/lib/chat/pdf';
import { transcribe } from '@/lib/chat/transcribe';
import { useI18n } from '@/lib/i18n';
import { useTheme } from '@/lib/theme';

type Pending = { key: string; name: string; state: 'uploading' | 'ready' | 'failed'; attachment?: Attachment; error?: string };

export function Composer({
    waiting,
    initialText,
    onSend,
    onStop,
}: {
    waiting: boolean;
    initialText?: string;
    onSend: (text: string, attachments: Attachment[]) => Promise<void>;
    onStop: () => Promise<void>;
}) {
    const { t } = useI18n();
    const theme = useTheme();
    const [text, setText] = useState(initialText ?? '');
    const [pending, setPending] = useState<Pending[]>([]);
    const [menu, setMenu] = useState(false);
    const [sending, setSending] = useState(false);
    const [notice, setNotice] = useState<string | null>(null);
    const [transcribing, setTranscribing] = useState(false);
    const recorder = useAudioRecorder(RecordingPresets.HIGH_QUALITY);
    const recording = useAudioRecorderState(recorder);

    useEffect(() => {
        if (initialText) setText(initialText);
    }, [initialText]);

    const add = async (name: string, size: number, body: () => Promise<Blob | Uint8Array>, mimeType?: string | null) => {
        const key = `${Date.now()}-${name}`;
        const refused = refusal({ name, size, mimeType });
        if (refused) {
            setNotice(refused === 'too_big' ? t('chat.attach.tooBig', { name }) : t('chat.attach.unsupported'));
            return;
        }
        setNotice(null);
        setPending((list) => [...list, { key, name, state: 'uploading' }]);
        try {
            const attachment = await uploadAttachment({ name, body: await body(), size, mimeType });
            setPending((list) => list.map((p) => (p.key === key ? { ...p, state: 'ready', attachment } : p)));
        } catch (e) {
            setPending((list) => list.map((p) => (p.key === key ? { ...p, state: 'failed', error: e instanceof Error ? e.message : String(e) } : p)));
        }
    };

    const addPhoto = async (asset: ImagePicker.ImagePickerAsset) => {
        try {
            if (!asset.base64) throw new Error('no data');
            const pdf = jpegToPdf(base64ToBytes(asset.base64));
            const base = (asset.fileName || 'photo').replace(/\.[^.]+$/, '');
            setNotice(t('chat.attach.photoNote'));
            await add(`${base}.pdf`, pdf.length, async () => pdf, 'application/pdf');
        } catch {
            setNotice(t('chat.attach.unsupported'));
        }
    };

    const pickCamera = async () => {
        setMenu(false);
        const permission = await ImagePicker.requestCameraPermissionsAsync();
        if (!permission.granted) return;
        const result = await ImagePicker.launchCameraAsync({ mediaTypes: ['images'], quality: 0.6, base64: true, exif: false });
        if (!result.canceled && result.assets[0]) await addPhoto(result.assets[0]);
    };

    const pickPhoto = async () => {
        setMenu(false);
        const result = await ImagePicker.launchImageLibraryAsync({ mediaTypes: ['images'], quality: 0.6, base64: true, exif: false });
        if (!result.canceled && result.assets[0]) await addPhoto(result.assets[0]);
    };

    const pickFile = async () => {
        setMenu(false);
        const result = await DocumentPicker.getDocumentAsync({ copyToCacheDirectory: true, multiple: false });
        if (result.canceled || !result.assets[0]) return;
        const asset = result.assets[0];
        await add(
            asset.name,
            asset.size ?? 0,
            async () => (asset.file ? asset.file : await (await fetch(asset.uri)).blob()),
            asset.mimeType,
        );
    };

    const openMenu = () => {
        if (Platform.OS === 'ios') {
            ActionSheetIOS.showActionSheetWithOptions(
                { options: [t('chat.attach.camera'), t('chat.attach.photos'), t('chat.attach.files'), t('common.cancel')], cancelButtonIndex: 3 },
                (i) => void [pickCamera, pickPhoto, pickFile][i]?.(),
            );
        } else setMenu((m) => !m);
    };

    const toggleVoiceNote = async () => {
        if (recording.isRecording) {
            const seconds = (recording.durationMillis ?? 0) / 1000;
            await recorder.stop();
            const uri = recorder.uri;
            if (!uri) return;
            setTranscribing(true);
            try {
                const words = await transcribe(uri);
                if (words) {
                    const block = `${t('chat.voiceNote.prefix', { duration: durationLabel(seconds) })}\n${words}`;
                    setText((current) => (current ? `${current}\n\n${block}` : block));
                }
            } catch (e) {
                setNotice(e instanceof Error ? e.message : String(e));
            } finally {
                setTranscribing(false);
            }
            return;
        }
        const permission = await AudioModule.requestRecordingPermissionsAsync();
        if (!permission.granted) {
            setNotice(t('voice.state.mic_denied'));
            return;
        }
        await setAudioModeAsync({ playsInSilentMode: true, allowsRecording: true });
        await recorder.prepareToRecordAsync();
        recorder.record();
    };

    const ready = pending.filter((p) => p.state === 'ready' && p.attachment).map((p) => p.attachment as Attachment);
    const uploading = pending.some((p) => p.state === 'uploading');
    const canSend = !sending && !uploading && !recording.isRecording && (text.trim().length > 0 || ready.length > 0);

    const send = async () => {
        if (!canSend) return;
        setSending(true);
        try {
            await onSend(text.trim(), ready);
            setText('');
            setPending([]);
            setNotice(null);
        } catch (e) {
            setNotice(e instanceof Error ? e.message : String(e));
        } finally {
            setSending(false);
        }
    };

    const iconButton = (name: keyof typeof Ionicons.glyphMap, label: string, onPress: () => void, testID: string, active?: boolean) => (
        <Pressable
            accessibilityRole="button"
            accessibilityLabel={label}
            onPress={onPress}
            testID={testID}
            hitSlop={6}
            style={{ width: 44, height: 44, alignItems: 'center', justifyContent: 'center', borderRadius: 22, backgroundColor: active ? theme.colors.bad : 'transparent' }}
        >
            <Ionicons name={name} size={24} color={active ? '#ffffff' : theme.colors.ink} />
        </Pressable>
    );

    return (
        <View style={{ borderTopWidth: 1, borderTopColor: theme.colors.line, padding: 8, gap: 6, backgroundColor: theme.colors.paper }} testID="composer">
            {menu ? (
                <View style={{ flexDirection: 'row', gap: 8, flexWrap: 'wrap' }}>
                    <Button label={t('chat.attach.camera')} kind="secondary" compact onPress={pickCamera} testID="attach-camera" />
                    <Button label={t('chat.attach.photos')} kind="secondary" compact onPress={pickPhoto} testID="attach-photos" />
                    <Button label={t('chat.attach.files')} kind="secondary" compact onPress={pickFile} testID="attach-files" />
                </View>
            ) : null}
            {pending.map((p) => (
                <View key={p.key} style={{ flexDirection: 'row', alignItems: 'center', gap: 8 }}>
                    <Ionicons name="document-attach-outline" size={18} color={theme.colors.ink2} />
                    <Txt size={13} tone={p.state === 'failed' ? 'bad' : 'ink2'} style={{ flex: 1 }} numberOfLines={2}>
                        {p.state === 'uploading' ? t('chat.attach.uploading', { name: p.name }) : p.state === 'ready' ? t('chat.attach.ready', { name: p.name }) : `${p.name}: ${p.error}`}
                    </Txt>
                    <Pressable accessibilityRole="button" accessibilityLabel={t('chat.attach.remove', { name: p.name })} onPress={() => setPending((list) => list.filter((x) => x.key !== p.key))} hitSlop={8}>
                        <Ionicons name="close" size={18} color={theme.colors.ink2} />
                    </Pressable>
                </View>
            ))}
            {notice ? (
                <Txt size={13} tone="ink2">
                    {notice}
                </Txt>
            ) : null}
            {recording.isRecording ? (
                <Txt size={13} tone="bad">
                    {t('chat.composer.recording', { seconds: Math.round((recording.durationMillis ?? 0) / 1000) })}
                </Txt>
            ) : null}
            {transcribing ? (
                <Txt size={13} tone="ink2">
                    {t('chat.composer.transcribing')}
                </Txt>
            ) : null}
            <View style={{ flexDirection: 'row', alignItems: 'flex-end', gap: 4 }}>
                {iconButton('add-circle-outline', t('chat.composer.attach'), openMenu, 'composer-attach')}
                <TextInput
                    testID="composer-input"
                    accessibilityLabel={t('chat.composer.placeholder')}
                    placeholder={t('chat.composer.placeholder')}
                    placeholderTextColor={theme.colors.ink3}
                    value={text}
                    onChangeText={setText}
                    multiline
                    style={{
                        flex: 1,
                        minHeight: 44,
                        maxHeight: 160,
                        borderWidth: 1,
                        borderColor: theme.colors.line,
                        borderRadius: 22,
                        paddingHorizontal: 14,
                        paddingTop: 11,
                        paddingBottom: 11,
                        fontSize: 16 * theme.scale,
                        color: theme.colors.ink,
                        backgroundColor: theme.colors.paper2,
                    }}
                />
                {iconButton(recording.isRecording ? 'stop' : 'mic-outline', t('chat.composer.voiceNote'), toggleVoiceNote, 'composer-voice-note', recording.isRecording)}
                {waiting ? (
                    iconButton('stop-circle-outline', t('chat.composer.stop'), () => void onStop(), 'composer-stop')
                ) : (
                    <Pressable
                        accessibilityRole="button"
                        accessibilityLabel={t('chat.composer.send')}
                        accessibilityState={{ disabled: !canSend }}
                        disabled={!canSend}
                        onPress={send}
                        testID="composer-send"
                        style={{ width: 44, height: 44, borderRadius: 22, alignItems: 'center', justifyContent: 'center', backgroundColor: theme.colors.primary, opacity: canSend ? 1 : 0.35 }}
                    >
                        <Ionicons name="arrow-up" size={22} color={theme.colors.onPrimary} />
                    </Pressable>
                )}
            </View>
        </View>
    );
}
