/**
 * A voice note into words: POST /workflow-recordings/transcribe, the same
 * endpoint the web composer's dictation uses (multipart `file` + `language`,
 * "unknown" so the provider detects Hindi, English or Hinglish itself).
 *
 * The generated client builds multipart from Blobs; on a phone a recording
 * is a file URI, which React Native's FormData sends as `{ uri, name, type }`.
 * So the generated function is called with a body serializer that knows
 * both -- still the generated endpoint, never a hand-written one.
 */
import { Platform } from 'react-native';

import { transcribeAudioApiV1WorkflowRecordingsTranscribePost } from '@/client/sdk.gen';
import { call } from '@/lib/api';

export async function transcribe(uri: string): Promise<string> {
    const name = Platform.OS === 'ios' ? 'note.m4a' : Platform.OS === 'android' ? 'note.m4a' : 'note.webm';
    const type = Platform.OS === 'web' ? 'audio/webm' : 'audio/m4a';
    const file: Blob = Platform.OS === 'web' ? await (await fetch(uri)).blob() : ({ uri, name, type } as unknown as Blob);
    const result = await call(
        transcribeAudioApiV1WorkflowRecordingsTranscribePost({
            body: { file, language: 'unknown' },
            bodySerializer: (raw: unknown) => {
                const body = raw as { file: Blob; language?: string };
                const form = new FormData();
                form.append('file', body.file as never, name);
                form.append('language', body.language ?? 'unknown');
                return form;
            },
        }),
    );
    return String((result as { transcript?: string }).transcript ?? '').trim();
}
