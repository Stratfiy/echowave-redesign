/**
 * The version list, with the undo for a bad publish.
 *
 * Guarded: every version that is not the draft offers Restore as draft, the
 * draft does not, and pressing it hands the version to the editor.
 */
import { fireEvent, render, screen } from '@testing-library/react';
import React from 'react';
import { describe, expect, it, vi } from 'vitest';

import { VersionHistoryPanel, type WorkflowVersion } from '../VersionHistoryPanel';

const version = (id: number, status: string): WorkflowVersion => ({
    id,
    version_number: id,
    status,
    created_at: new Date().toISOString(),
    published_at: null,
    workflow_json: {},
    workflow_configurations: null,
    template_context_variables: null,
});

describe('restoring', () => {
    it('is offered on every version but the draft, and hands the version over', () => {
        const restore = vi.fn();
        render(
            <VersionHistoryPanel
                isOpen
                onClose={() => {}}
                versions={[version(3, 'draft'), version(2, 'published'), version(1, 'archived')]}
                loading={false}
                activeVersionId={3}
                onSelectVersion={() => {}}
                onRestoreVersion={restore}
                hasMore={false}
                loadingMore={false}
                onLoadMore={() => {}}
            />,
        );
        expect(screen.queryByRole('button', { name: 'Restore v3' })).toBeNull();
        expect(screen.getByRole('button', { name: 'Restore v2' })).toBeTruthy();
        fireEvent.click(screen.getByRole('button', { name: 'Restore v1' }));
        expect(restore).toHaveBeenCalledWith(expect.objectContaining({ id: 1 }));
    });
});
