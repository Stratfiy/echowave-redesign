/**
 * A life-stage starter on the Chat start names the helper that does its job
 * (api/services/workflow/home_openers.py). Choosing it fills the box and
 * chooses that helper, so the message goes to the helper -- while helpers
 * are on. Off, the words still fill the box and go to Automatic.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ChannelComposer } from '../ChannelComposer';

const post = vi.hoisted(() => vi.fn());
const flags = vi.hoisted(() => ({ launch_helpers: true }));
vi.mock('@/client/sdk.gen', () => ({
    postMessageApiV1TimelineMessagePost: post,
    translateTextApiV1TranslatePost: vi.fn(),
    transcribeAudioApiV1WorkflowRecordingsTranscribePost: vi.fn(),
    listFoldersApiV1FolderGet: async () => ({ data: [] }),
    brainsApiV1TimelineBrainsGet: async () => ({ data: { presets: [], vendors: [] } }),
    memoryApiV1TimelineMemoryGet: async () => ({ data: null }),
}));
vi.mock('@/lib/features', () => ({
    useFeature: (name: string) => Boolean((flags as Record<string, boolean>)[name]),
}));
vi.mock('@/components/helpers/HelperPicker', () => ({
    HelperPicker: () => null,
    HelperChip: ({ helper }: { helper: { name: string } }) => <span data-testid="helper-chip">{helper.name}</span>,
}));

const STARTER = {
    text: 'Plan my revision back from my exam date',
    id: 1,
    helper: { key: 'learning_guide', name: 'Learning Guide' },
};

beforeEach(() => {
    post.mockReset();
    post.mockResolvedValue({ data: { asked: [], unknown: [], ambiguous: [] } });
    flags.launch_helpers = true;
    localStorage.clear();
});

describe('a starter that names a helper', () => {
    it('fills the box, chooses the helper, and sends to it', async () => {
        render(<ChannelComposer assistant threadId="t-1" bots={[]} channelName="Decibyl" chatShell draftRequest={STARTER} />);
        expect((screen.getByRole('textbox') as HTMLTextAreaElement).value).toBe(STARTER.text);
        expect(screen.getByTestId('helper-chip').textContent).toBe('Learning Guide');
        fireEvent.click(screen.getByRole('button', { name: 'Send' }));
        await waitFor(() => expect(post).toHaveBeenCalled());
        expect(post.mock.calls[0][0].body.helper).toBe('learning_guide');
    });

    it('with helpers off, fills the box and sends to Automatic', async () => {
        flags.launch_helpers = false;
        render(<ChannelComposer assistant threadId="t-1" bots={[]} channelName="Decibyl" chatShell draftRequest={STARTER} />);
        expect(screen.queryByTestId('helper-chip')).toBeNull();
        fireEvent.click(screen.getByRole('button', { name: 'Send' }));
        await waitFor(() => expect(post).toHaveBeenCalled());
        expect(post.mock.calls[0][0].body.helper).toBeUndefined();
    });
});
