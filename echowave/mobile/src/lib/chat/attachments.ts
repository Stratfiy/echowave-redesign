/**
 * Attaching a file to a Decibyl message, the same three steps as the web
 * composer (ui/src/lib/uploadKnowledge.ts): ask for a presigned upload URL,
 * PUT the bytes there, then have the knowledge base read it. The message
 * then names the document by uuid; the server checks it belongs to the
 * workspace.
 *
 * Done when the file is picked, not on send, so a slow upload shows while
 * the person is still typing.
 */
import {
    getUploadUrlApiV1KnowledgeBaseUploadUrlPost,
    processDocumentApiV1KnowledgeBaseProcessDocumentPost,
} from '@/client/sdk.gen';
import { call } from '@/lib/api';

import type { Attachment } from './events';

export const MAX_BYTES = 5 * 1024 * 1024;
export const READABLE = ['.pdf', '.docx', '.txt', '.md', '.json', '.csv', '.html'];

const MIME_BY_EXT: Record<string, string> = {
    '.pdf': 'application/pdf',
    '.docx': 'application/vnd.openxmlformats-officedocument.wordprocessingml.document',
    '.txt': 'text/plain',
    '.md': 'text/markdown',
    '.json': 'application/json',
    '.csv': 'text/csv',
    '.html': 'text/html',
};

export function extensionOf(name: string): string {
    const m = /\.[A-Za-z0-9]+$/.exec(name.trim());
    return m ? m[0].toLowerCase() : '';
}

export type Pickable = { name: string; size?: number | null; mimeType?: string | null };

export type Refusal = 'too_big' | 'unsupported' | null;

export function refusal(file: Pickable): Refusal {
    if (file.size != null && file.size > MAX_BYTES) return 'too_big';
    if (!READABLE.includes(extensionOf(file.name))) return 'unsupported';
    return null;
}

export type UploadBody = Blob | Uint8Array;

export async function uploadAttachment(options: {
    name: string;
    body: UploadBody;
    size: number;
    mimeType?: string | null;
    put?: (url: string, body: UploadBody, contentType: string) => Promise<Response>;
}): Promise<Attachment> {
    const contentType = options.mimeType || MIME_BY_EXT[extensionOf(options.name)] || 'application/octet-stream';
    const slot = await call(
        getUploadUrlApiV1KnowledgeBaseUploadUrlPost({
            body: { filename: options.name, mime_type: contentType },
        }),
    );
    const put =
        options.put ??
        ((url: string, body: UploadBody, type: string) =>
            fetch(url, { method: 'PUT', body: body as BodyInit, headers: { 'Content-Type': type } }));
    const response = await put(slot.upload_url, options.body, contentType);
    if (!response.ok) throw new Error(`The upload did not go through (${response.status}).`);
    await call(
        processDocumentApiV1KnowledgeBaseProcessDocumentPost({
            body: {
                document_uuid: slot.document_uuid,
                s3_key: slot.s3_key,
                retrieval_mode: 'full_document',
                scope: 'org',
            },
        }),
    );
    return { document_uuid: slot.document_uuid, filename: options.name, size_bytes: options.size };
}

/** "Voice note (0:42), transcribed:\n..." -- the same block the web folds
 * into the text (ui/src/lib/shell/composerBlocks.ts). */
export function durationLabel(seconds: number): string {
    const s = Math.max(0, Math.round(seconds));
    return `${Math.floor(s / 60)}:${String(s % 60).padStart(2, '0')}`;
}
