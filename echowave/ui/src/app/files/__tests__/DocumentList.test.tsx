/**
 * A copy of a document in another language, from the list: a Translate
 * menu on a document that has been read, none on one still being read.
 */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const list = vi.hoisted(() => vi.fn());
const usage = vi.hoisted(() => vi.fn());
const translate = vi.hoisted(() => vi.fn());
vi.mock('@/client/sdk.gen', () => ({
    listDocumentsApiV1KnowledgeBaseDocumentsGet: list,
    getUsageApiV1KnowledgeBaseUsageGet: usage,
    deleteDocumentApiV1KnowledgeBaseDocumentsDocumentUuidDelete: vi.fn(),
    translateDocumentRouteApiV1KnowledgeBaseDocumentsDocumentUuidTranslatePost: translate,
}));
vi.mock('sonner', () => ({ toast: { success: vi.fn(), error: vi.fn() } }));

import DocumentList from '../DocumentList';

const doc = (over: Record<string, unknown>) => ({
    id: 1,
    document_uuid: 'd1',
    filename: 'rates.pdf',
    file_size_bytes: 1024,
    file_hash: '',
    mime_type: 'application/pdf',
    processing_status: 'completed',
    processing_error: null,
    needs_reingest: false,
    total_chunks: 3,
    retrieval_mode: 'chunked',
    custom_metadata: {},
    docling_metadata: {},
    source_url: null,
    scope: 'org',
    folder_id: null,
    workflow_id: null,
    created_at: '2026-09-14T00:00:00Z',
    updated_at: '2026-09-14T00:00:00Z',
    organization_id: 7,
    created_by: 1,
    is_active: true,
    ...over,
});

beforeEach(() => {
    list.mockReset();
    translate.mockReset();
    usage.mockResolvedValue({ data: { bytes_used: 0, bytes_limit: 0 } });
});

describe('translating a document from the list', () => {
    it('offers the languages on a read document and starts the copy', async () => {
        list.mockResolvedValue({ data: { documents: [doc({})], total: 1, limit: 100, offset: 0 } });
        translate.mockResolvedValue({ data: doc({ id: 2, document_uuid: 'd2', filename: 'rates (Hindi).txt', processing_status: 'pending' }) });
        render(<DocumentList refreshTrigger={0} />);
        const menu = await screen.findByRole('button', { name: 'Translate rates.pdf' });
        fireEvent.keyDown(menu, { key: 'Enter' });
        fireEvent.click(await screen.findByText('Hindi'));
        await waitFor(() =>
            expect(translate).toHaveBeenCalledWith({ path: { document_uuid: 'd1' }, body: { target_language_code: 'hi-IN' } }),
        );
    });

    it('offers nothing on a document still being read', async () => {
        list.mockResolvedValue({ data: { documents: [doc({ processing_status: 'processing' })], total: 1, limit: 100, offset: 0 } });
        render(<DocumentList refreshTrigger={0} />);
        await screen.findByText('rates.pdf');
        expect(screen.queryByRole('button', { name: /Translate/ })).toBeNull();
    });

    it('says when a document is a translation of another', async () => {
        list.mockResolvedValue({
            data: { documents: [doc({ filename: 'rates (English).txt', custom_metadata: { translated_from: 'src' } })], total: 1, limit: 100, offset: 0 },
        });
        render(<DocumentList refreshTrigger={0} />);
        expect(await screen.findByText('A translation of another document here')).toBeTruthy();
    });
});
