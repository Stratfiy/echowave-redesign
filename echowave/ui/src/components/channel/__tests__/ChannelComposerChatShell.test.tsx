/**
 * The composer under `chat_shell` (screens 03-04): the attach menu, Dictate
 * apart from Talk, Stop in place of Send while a reply forms, starters that
 * fill the box, notes that ride with the message, and a draft kept across
 * going offline.
 */

import { act, fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { registerMeetingMode, registerTalk, resetEntryPoints } from '@/lib/shell/chatEntryPoints';

import { ChannelComposer } from '../ChannelComposer';

const post = vi.hoisted(() => vi.fn());
const memory = vi.hoisted(() => vi.fn());
vi.mock('@/client/sdk.gen', () => ({
    postMessageApiV1TimelineMessagePost: post,
    translateTextApiV1TranslatePost: vi.fn(),
    transcribeAudioApiV1WorkflowRecordingsTranscribePost: vi.fn(),
    listFoldersApiV1FolderGet: async () => ({ data: [] }),
    brainsApiV1TimelineBrainsGet: async () => ({ data: { presets: [], vendors: [] } }),
    memoryApiV1TimelineMemoryGet: memory,
}));
vi.mock('@/lib/features', () => ({ useFeature: () => false }));

function composer(props: Partial<React.ComponentProps<typeof ChannelComposer>> = {}) {
    return render(<ChannelComposer assistant threadId="t-1" bots={[]} channelName="Decibyl" chatShell {...props} />);
}

beforeEach(() => {
    post.mockReset();
    memory.mockReset();
    memory.mockResolvedValue({ data: null });
    post.mockResolvedValue({ data: { asked: [], unknown: [], ambiguous: [] } });
    localStorage.clear();
});
afterEach(() => resetEntryPoints());

async function openAttach() {
    fireEvent.keyDown(screen.getByRole('button', { name: 'Attach' }), { key: 'Enter' });
    return screen.findByText('Files');
}

describe('the attach menu', () => {
    it('offers files, voice note, meeting mode and paste notes', async () => {
        composer();
        await openAttach();
        expect(screen.getByText('Voice note')).toBeTruthy();
        expect(screen.getByText('Meeting mode')).toBeTruthy();
        expect(screen.getByText('Paste notes')).toBeTruthy();
        // No handler from the meetings stream yet: said, not silent.
        expect(screen.getByText('Not available yet')).toBeTruthy();
    });

    it('opens meeting mode through its registered entry point', async () => {
        const open = vi.fn();
        registerMeetingMode(open);
        composer();
        await openAttach();
        fireEvent.click(screen.getByText('Meeting mode'));
        expect(open).toHaveBeenCalledWith({ threadId: 't-1', draft: '' });
    });

    it('sends pasted notes as their own labelled block with the question', async () => {
        composer();
        await openAttach();
        fireEvent.click(screen.getByText('Paste notes'));
        fireEvent.change(await screen.findByLabelText('Notes'), { target: { value: 'Ravi owes 4000 since August' } });
        fireEvent.click(screen.getByRole('button', { name: 'Add notes' }));
        expect(await screen.findByText('Notes (5 words)')).toBeTruthy();
        fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Draft a reminder' } });
        fireEvent.click(screen.getByRole('button', { name: 'Send' }));
        await waitFor(() => expect(post).toHaveBeenCalled());
        expect(post.mock.calls[0][0].body.text).toBe('Draft a reminder\n\nNotes:\nRavi owes 4000 since August');
        await waitFor(() => expect(screen.queryByText('Notes (5 words)')).toBeNull());
    });
});

describe('Dictate and Talk', () => {
    it('are two different controls', () => {
        composer();
        expect(screen.getByRole('button', { name: 'Dictate' })).toBeTruthy();
        expect(screen.getByRole('button', { name: /Talk: a live voice conversation/ })).toBeTruthy();
    });

    it('Talk says why it cannot start until the voice stream registers', () => {
        composer();
        const talk = screen.getByRole('button', { name: /Talk/ });
        expect(talk.getAttribute('aria-disabled')).toBe('true');
        fireEvent.click(talk);
        expect(screen.getByText(/Live voice is not available yet/)).toBeTruthy();
    });

    it('Talk opens the voice session with the thread and the draft', () => {
        const open = vi.fn();
        composer();
        act(() => {
            registerTalk(open);
        });
        fireEvent.change(screen.getByRole('textbox'), { target: { value: 'about the invoice' } });
        fireEvent.click(screen.getByRole('button', { name: /Talk/ }));
        expect(open).toHaveBeenCalledWith({ threadId: 't-1', draft: 'about the invoice' });
    });
});

describe('Stop', () => {
    it('replaces Send while a reply is forming', () => {
        const onStop = vi.fn();
        const { rerender } = composer({ replying: true, onStop });
        expect(screen.queryByRole('button', { name: 'Send' })).toBeNull();
        fireEvent.click(screen.getByRole('button', { name: 'Stop' }));
        expect(onStop).toHaveBeenCalledOnce();
        rerender(<ChannelComposer assistant threadId="t-1" bots={[]} channelName="Decibyl" chatShell replying={false} onStop={onStop} />);
        expect(screen.getByRole('button', { name: 'Send' })).toBeTruthy();
    });
});

describe('starters and drafts', () => {
    it('a starter fills the box with editable words and sends nothing', () => {
        const { rerender } = composer({ draftRequest: { text: 'Help me plan today', id: 1 } });
        const box = screen.getByRole('textbox') as HTMLTextAreaElement;
        expect(box.value).toBe('Help me plan today');
        fireEvent.change(box, { target: { value: 'Help me plan tomorrow' } });
        expect(box.value).toBe('Help me plan tomorrow');
        expect(post).not.toHaveBeenCalled();
        // The same starter again still fills it.
        rerender(<ChannelComposer assistant threadId="t-1" bots={[]} channelName="Decibyl" chatShell draftRequest={{ text: 'Help me plan today', id: 2 }} />);
        expect(box.value).toBe('Help me plan today');
    });

    it('keeps the draft for this conversation across a reload, and clears it once sent', async () => {
        const first = composer();
        fireEvent.change(screen.getByRole('textbox'), { target: { value: 'half a thought' } });
        first.unmount();
        composer();
        const box = screen.getByRole('textbox') as HTMLTextAreaElement;
        expect(box.value).toBe('half a thought');
        fireEvent.click(screen.getByRole('button', { name: 'Send' }));
        await waitFor(() => expect(box.value).toBe(''));
        expect(localStorage.length).toBe(0);
    });

    it('says the draft is kept when the browser goes offline', () => {
        composer();
        act(() => {
            window.dispatchEvent(new Event('offline'));
        });
        expect(screen.getByTestId('offline-notice').textContent).toContain('Your draft is kept');
        act(() => {
            window.dispatchEvent(new Event('online'));
        });
        expect(screen.queryByTestId('offline-notice')).toBeNull();
    });

    it('writes at 16px on a phone and gives every control a 44px target', () => {
        composer();
        expect(screen.getByRole('textbox').className).toContain('text-base');
        expect(screen.getByRole('button', { name: 'Send' }).className).toContain('size-11');
        expect(screen.getByRole('button', { name: 'Attach' }).className).toContain('size-11');
        expect(screen.getByRole('button', { name: 'Dictate' }).className).toContain('min-h-11');
    });
});

describe("a member's first message from the start screen", () => {
    // Phase 3: with private threads on the original conversation is not a
    // plain member's, and sending to it failed with "Thread not found".
    it('starts a new conversation of their own and says which', async () => {
        const onSent = vi.fn();
        composer({ threadId: null, startsNewThread: true, onSent });
        fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Plan my week' } });
        fireEvent.click(screen.getByRole('button', { name: 'Send' }));
        await waitFor(() => expect(onSent).toHaveBeenCalled());
        const sentTo = post.mock.calls[0][0].body.thread_id;
        expect(sentTo).toMatch(/^[0-9a-f-]{36}$/);
        expect(onSent).toHaveBeenCalledWith([], sentTo);
    });

    it('never mints inside a conversation that already exists', async () => {
        const onSent = vi.fn();
        composer({ threadId: 't-1', startsNewThread: true, onSent });
        fireEvent.change(screen.getByRole('textbox'), { target: { value: 'More' } });
        fireEvent.click(screen.getByRole('button', { name: 'Send' }));
        await waitFor(() => expect(onSent).toHaveBeenCalledWith([], undefined));
        expect(post.mock.calls[0][0].body.thread_id).toBe('t-1');
    });

    it('without it, the start screen still writes to the original', async () => {
        composer({ threadId: null });
        fireEvent.change(screen.getByRole('textbox'), { target: { value: 'Hello' } });
        fireEvent.click(screen.getByRole('button', { name: 'Send' }));
        await waitFor(() => expect(post).toHaveBeenCalled());
        expect(post.mock.calls[0][0].body.thread_id).toBeNull();
    });
});

describe('the memory meter', () => {
    // Phase 3: the meter stayed on the conversation it was first read for,
    // and on a member's start screen it read -- and showed -- the size of a
    // conversation that was not theirs.
    it('is read again for the conversation now on screen', async () => {
        const view = composer({ threadId: 't-1' });
        await waitFor(() => expect(memory).toHaveBeenCalledTimes(1));
        view.rerender(<ChannelComposer assistant threadId="t-2" bots={[]} channelName="Decibyl" chatShell />);
        await waitFor(() => expect(memory).toHaveBeenCalledTimes(2));
        expect(memory.mock.calls[1][0].query.thread_id).toBe('t-2');
    });

    it('is not read for an original conversation that is not theirs', async () => {
        composer({ threadId: null, startsNewThread: true });
        await new Promise((resolve) => setTimeout(resolve, 20));
        expect(memory).not.toHaveBeenCalled();
    });

    it('waits until it is known whose the original is, then reads it', async () => {
        const view = composer({ threadId: null, originalUnknown: true });
        await new Promise((resolve) => setTimeout(resolve, 20));
        expect(memory).not.toHaveBeenCalled();
        view.rerender(<ChannelComposer assistant threadId={null} bots={[]} channelName="Decibyl" chatShell />);
        await waitFor(() => expect(memory).toHaveBeenCalledTimes(1));
    });
});

describe('with the flag off', () => {
    it('is the composer it was: a paperclip, a mic, and no Talk', () => {
        render(<ChannelComposer assistant bots={[]} channelName="Decibyl" />);
        expect(screen.getByRole('button', { name: 'Attach a file' })).toBeTruthy();
        expect(screen.getByRole('button', { name: 'Speak' })).toBeTruthy();
        expect(screen.queryByRole('button', { name: /Talk/ })).toBeNull();
        expect(screen.queryByRole('button', { name: 'Attach' })).toBeNull();
    });
});
