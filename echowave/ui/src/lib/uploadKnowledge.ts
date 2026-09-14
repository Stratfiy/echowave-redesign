/**
 * One upload, start to finish: mint the presigned URL, put the bytes, tell
 * the API to read the document. Used by the Company knowledge page and by
 * the composer's paperclip, so a file dropped into a chat goes through
 * exactly the door a file uploaded on the knowledge page does -- the same
 * key check, the same quota, the same scope check.
 */

import {
    getUploadUrlApiV1KnowledgeBaseUploadUrlPost,
    processDocumentApiV1KnowledgeBaseProcessDocumentPost,
} from '@/client/sdk.gen';

export const MAX_FILE_SIZE = 5 * 1024 * 1024; // 5MB
// Kept in step with api/services/knowledge_base/extraction.py. Legacy .doc is
// deliberately absent: no pure-Python reader handles it, so offering it here
// only produces an upload that fails after the customer has waited for it.
export const ACCEPTED_FILE_TYPES = ['.pdf', '.docx', '.txt', '.md', '.json', '.csv', '.html'];

/** Who a document is knowledge for. Mirrors api.enums.KnowledgeScope. */
export type KnowledgeScope = 'library' | 'org' | 'channel' | 'bot';

export type KnowledgeTarget =
    | { scope: 'library' }
    | { scope: 'org' }
    | { scope: 'channel'; folderId: number }
    | { scope: 'bot'; workflowId: number };

export type Uploaded = { document_uuid: string; filename: string; size_bytes: number };

/** Why a file cannot be uploaded, or null when it can. */
export function rejectFile(file: File): string | null {
    const extension = '.' + file.name.split('.').pop()?.toLowerCase();
    if (!ACCEPTED_FILE_TYPES.includes(extension)) {
        return `Please select a supported file type: ${ACCEPTED_FILE_TYPES.join(', ')}`;
    }
    if (file.size > MAX_FILE_SIZE) return 'File size must be less than 5MB';
    return null;
}

export async function uploadKnowledge(
    file: File,
    target: KnowledgeTarget,
    options: { retrievalMode?: string; onProgress?: (percent: number) => void } = {},
): Promise<Uploaded> {
    const progress = options.onProgress ?? (() => {});
    const minted = await getUploadUrlApiV1KnowledgeBaseUploadUrlPost({
        body: {
            filename: file.name,
            mime_type: file.type || 'application/octet-stream',
            custom_metadata: {
                original_filename: file.name,
                uploaded_at: new Date().toISOString(),
            },
        },
    });
    if (minted.error || !minted.data) throw new Error('Failed to get upload URL');
    progress(25);

    const put = await fetch(minted.data.upload_url, {
        method: 'PUT',
        body: file,
        headers: { 'Content-Type': file.type || 'application/octet-stream' },
    });
    if (!put.ok) throw new Error('Failed to upload file to storage');
    progress(75);

    const processed = await processDocumentApiV1KnowledgeBaseProcessDocumentPost({
        body: {
            document_uuid: minted.data.document_uuid,
            s3_key: minted.data.s3_key,
            retrieval_mode: options.retrievalMode ?? 'full_document',
            scope: target.scope,
            folder_id: target.scope === 'channel' ? target.folderId : null,
            workflow_id: target.scope === 'bot' ? target.workflowId : null,
        },
    });
    if (processed.error) throw new Error('Failed to trigger processing');
    progress(100);
    return { document_uuid: minted.data.document_uuid, filename: file.name, size_bytes: file.size };
}
