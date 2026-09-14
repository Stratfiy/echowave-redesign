/**
 * The composer, which is where @mentions become usable rather than merely
 * implemented.
 *
 * Two behaviours carry weight here and both are about not leaving somebody
 * waiting on a reply that was never coming:
 *
 * - the autocomplete, because a handle nobody can find is a handle nobody
 *   uses, and the alternative is typing a half-remembered name and getting
 *   silence;
 * - saying out loud when the server refused to guess. `unknown` and
 *   `ambiguous` come back precisely so a screen can say what happened, and a
 *   screen that drops them turns the server's honesty back into silence.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import { ChannelComposer, handleOf, mentionFragment } from '../ChannelComposer';

const post = vi.hoisted(() => vi.fn());
const translate = vi.hoisted(() => vi.fn());
const transcribe = vi.hoisted(() => vi.fn());
vi.mock('@/client/sdk.gen', () => ({
    postMessageApiV1TimelineMessagePost: post,
    translateTextApiV1TranslatePost: translate,
    transcribeAudioApiV1WorkflowRecordingsTranscribePost: transcribe,
}));
const upload = vi.hoisted(() => vi.fn());
vi.mock('@/lib/uploadKnowledge', async (importOriginal) => ({
    ...(await importOriginal<typeof import('@/lib/uploadKnowledge')>()),
    uploadKnowledge: upload,
}));

const BOTS = [
    { id: 1, name: 'Narayani Dental front desk', handle: 'front' },
    { id: 2, name: 'Sales bot', handle: 'sales' },
    { id: 3, name: 'Supplier chaser', handle: null },
];

function composer(onSent = () => {}) {
    return render(
        <ChannelComposer
            folderId={9}
            bots={BOTS}
            channelName="clinic"
            onSent={onSent}
        />,
    );
}

beforeEach(() => {
    post.mockReset();
    post.mockResolvedValue({ data: { asked: [1], unknown: [], ambiguous: [] } });
    upload.mockReset();
    translate.mockReset();
    // The brain picked for a chat is remembered on the device; one test's
    // choice must not become the next test's default.
    localStorage.clear();
});

describe('a file is a message', () => {
    it('uploads a picked file as knowledge for this chat, and sends it with the message', async () => {
        upload.mockResolvedValue({ document_uuid: 'd1', filename: 'rates.pdf', size_bytes: 12 });
        render(<ChannelComposer workflowId={3} bots={[]} channelName="Front desk" />);
        const picker = screen.getByLabelText('Attach a file', { selector: 'input' });
        const file = new File(['x'], 'rates.pdf', { type: 'application/pdf' });
        fireEvent.change(picker, { target: { files: [file] } });
        // Scoped to this bot, not to company knowledge.
        await waitFor(() => expect(upload).toHaveBeenCalled());
        expect(upload.mock.calls[0][1]).toEqual({ scope: 'bot', workflowId: 3 });
        expect(await screen.findByText('rates.pdf')).toBeTruthy();
        // No words needed: the send button is live with a file alone.
        fireEvent.click(screen.getByRole('button', { name: 'Send' }));
        await waitFor(() => expect(post).toHaveBeenCalled());
        expect(post.mock.calls[0][0].body).toEqual({
            workflow_id: 3,
            text: '',
            attachments: [{ document_uuid: 'd1', filename: 'rates.pdf', size_bytes: 12 }],
            preset: null,
        });
    });

    it('in a channel the file is the channel\'s', async () => {
        upload.mockResolvedValue({ document_uuid: 'd2', filename: 'menu.pdf', size_bytes: 1 });
        composer();
        const picker = screen.getByLabelText('Attach a file', { selector: 'input' });
        fireEvent.change(picker, { target: { files: [new File(['x'], 'menu.pdf')] } });
        await waitFor(() => expect(upload).toHaveBeenCalled());
        expect(upload.mock.calls[0][1]).toEqual({ scope: 'channel', folderId: 9 });
    });

    it('remembers the brain picked for this chat and sends it with the message', async () => {
        composer();
        fireEvent.keyDown(screen.getByRole('button', { name: 'Brain for this message' }), { key: 'Enter' });
        fireEvent.click(await screen.findByText('Deep'));
        const box = screen.getByRole('textbox') as HTMLTextAreaElement;
        fireEvent.change(box, { target: { value: 'think' } });
        fireEvent.click(screen.getByRole('button', { name: 'Send' }));
        await waitFor(() => expect(post).toHaveBeenCalled());
        expect(post.mock.calls[0][0].body.preset).toBe('deep');
        expect(JSON.parse(localStorage.getItem('decibyl.chat-preset') || '{}')).toEqual({ 'channel:9': 'deep' });
    });

    it('refuses a file type nothing can read, before uploading', async () => {
        composer();
        const picker = screen.getByLabelText('Attach a file', { selector: 'input' });
        fireEvent.change(picker, { target: { files: [new File(['x'], 'photo.png')] } });
        expect(await screen.findByRole('alert')).toBeTruthy();
        expect(upload).not.toHaveBeenCalled();
    });
});

describe('the handle a bot answers to', () => {
    it('is the stored one when there is one', () => {
        expect(handleOf(BOTS[0])).toBe('front');
    });

    it('falls back to the name when there is not', () => {
        // The same fallback the server applies. Without it a bot whose handle
        // was never assigned would be offered no address at all, which is
        // indistinguishable on screen from having none.
        expect(handleOf(BOTS[2])).toBe('supplier-chaser');
    });

    it('folds accents, because the handle gets typed on a phone keyboard', () => {
        expect(handleOf({ id: 4, name: 'Meerá' })).toBe('meera');
    });
});

describe('where a mention starts', () => {
    it('at the beginning of the message', () => {
        expect(mentionFragment('@fro', 4)).toBe('fro');
    });

    it('after a space', () => {
        expect(mentionFragment('please ask @sal', 15)).toBe('sal');
    });

    it('never mid-word, so an email address offers nothing', () => {
        // Typing "ramesh@clinic.example" must not pop a bot list. This is the
        // same boundary the server's regex applies.
        expect(mentionFragment('mail ramesh@clin', 16)).toBeNull();
    });

    it('is null when the caret has moved past the mention', () => {
        expect(mentionFragment('@front are we open', 18)).toBeNull();
    });
});

describe('offering the roster', () => {
    it('suggests the bots in this channel as you type', async () => {
        composer();
        const box = screen.getByLabelText('Message clinic');
        fireEvent.change(box, { target: { value: '@f' } });
        expect(await screen.findByText('Narayani Dental front desk')).toBeTruthy();
        expect(screen.queryByText('Sales bot')).toBeNull();
    });

    it('completes to the handle, not to the display name', async () => {
        composer();
        const box = screen.getByLabelText('Message clinic') as HTMLTextAreaElement;
        fireEvent.change(box, { target: { value: '@f' } });
        fireEvent.mouseDown(await screen.findByText('Narayani Dental front desk'));
        await waitFor(() => expect(box.value).toBe('@front '));
    });

    it('offers a handle-less bot the address its name implies', async () => {
        composer();
        const box = screen.getByLabelText('Message clinic');
        fireEvent.change(box, { target: { value: '@supp' } });
        expect(await screen.findByText('@supplier-chaser')).toBeTruthy();
    });
});

describe('the @ button', () => {
    it('types an @ and opens the roster', async () => {
        composer();
        fireEvent.mouseDown(screen.getByLabelText('Mention a bot'));
        const box = screen.getByLabelText('Message clinic') as HTMLTextAreaElement;
        await waitFor(() => expect(box.value).toBe('@'));
        // The roster opens on the empty fragment: every bot in the channel.
        expect(await screen.findByText('Narayani Dental front desk')).toBeTruthy();
        expect(screen.getByText('Sales bot')).toBeTruthy();
    });

    it('puts a space first when the caret is mid-word', async () => {
        composer();
        const box = screen.getByLabelText('Message clinic') as HTMLTextAreaElement;
        fireEvent.change(box, { target: { value: 'ask' } });
        box.setSelectionRange(3, 3);
        fireEvent.mouseDown(screen.getByLabelText('Mention a bot'));
        // "ask@" would not be a mention by the server's rule; "ask @" is.
        await waitFor(() => expect(box.value).toBe('ask @'));
    });
});

describe('sending', () => {
    it('posts the message to this channel', async () => {
        composer();
        const box = screen.getByLabelText('Message clinic');
        fireEvent.change(box, { target: { value: '@front are we open?' } });
        fireEvent.click(screen.getByLabelText('Send'));
        await waitFor(() =>
            expect(post).toHaveBeenCalledWith({
                body: { folder_id: 9, text: '@front are we open?', attachments: [], preset: null },
            }),
        );
    });

    it('clears the box only after the server has it', async () => {
        composer();
        const box = screen.getByLabelText('Message clinic') as HTMLTextAreaElement;
        fireEvent.change(box, { target: { value: 'hello' } });
        fireEvent.click(screen.getByLabelText('Send'));
        await waitFor(() => expect(box.value).toBe(''));
    });

    it('says so when nobody here answers to that handle', async () => {
        // The server returns `unknown` so this sentence can exist. Dropping it
        // leaves somebody waiting on a reply that was never coming.
        post.mockResolvedValue({
            data: { asked: [], unknown: ['op-bot'], ambiguous: [] },
        });
        composer();
        fireEvent.change(screen.getByLabelText('Message clinic'), {
            target: { value: '@op-bot chase it' },
        });
        fireEvent.click(screen.getByLabelText('Send'));
        const said = await screen.findByRole('status');
        expect(said.textContent).toContain('@op-bot');
    });

    it('says so when two bots answer to the same handle', async () => {
        post.mockResolvedValue({
            data: { asked: [], unknown: [], ambiguous: ['sales'] },
        });
        composer();
        fireEvent.change(screen.getByLabelText('Message clinic'), {
            target: { value: '@sales send it' },
        });
        fireEvent.click(screen.getByLabelText('Send'));
        const said = await screen.findByRole('status');
        expect(said.textContent).toContain('More than one');
    });

    it('surfaces a failure rather than looking sent', async () => {
        post.mockResolvedValue({ error: { detail: 'No credit' } });
        composer();
        const box = screen.getByLabelText('Message clinic') as HTMLTextAreaElement;
        fireEvent.change(box, { target: { value: 'hello' } });
        fireEvent.click(screen.getByLabelText('Send'));
        expect(await screen.findByRole('alert')).toBeTruthy();
        // Still there to retry, rather than cleared into nothing.
        expect(box.value).toBe('hello');
    });

    it('sends nothing when the box holds only whitespace', () => {
        composer();
        fireEvent.change(screen.getByLabelText('Message clinic'), {
            target: { value: '   ' },
        });
        fireEvent.click(screen.getByLabelText('Send'));
        expect(post).not.toHaveBeenCalled();
    });

    it('shift+enter writes a newline instead of sending', () => {
        composer();
        const box = screen.getByLabelText('Message clinic');
        fireEvent.change(box, { target: { value: 'first line' } });
        fireEvent.keyDown(box, { key: 'Enter', shiftKey: true });
        expect(post).not.toHaveBeenCalled();
    });
});

describe("on a bot's own chat", () => {
    it('sends to the bot, with nobody to mention', async () => {
        post.mockResolvedValue({ data: { asked: [3], unknown: [], ambiguous: [] } });
        render(<ChannelComposer workflowId={3} bots={[]} channelName="Front desk" />);
        expect(screen.queryByLabelText('Mention a bot')).toBeNull();
        const box = screen.getByLabelText('Message Front desk');
        fireEvent.change(box, { target: { value: 'Book Meera at 4' } });
        fireEvent.keyDown(box, { key: 'Enter' });
        await waitFor(() =>
            expect(post).toHaveBeenCalledWith({ body: { workflow_id: 3, text: 'Book Meera at 4', attachments: [], preset: null } }),
        );
    });
});


describe('a draft in an Indian script', () => {
    it('is offered English and Roman script, and the draft becomes the answer', async () => {
        translate.mockResolvedValue({ data: { text: 'Book Meera at 4' } });
        composer();
        const box = screen.getByRole('textbox') as HTMLTextAreaElement;
        expect(screen.queryByLabelText('Language offers')).toBeNull();
        fireEvent.change(box, { target: { value: 'मीरा को 4 बजे बुक करो' } });
        expect(screen.getByLabelText('Language offers')).toBeTruthy();
        fireEvent.click(screen.getByRole('button', { name: 'Translate to English' }));
        await waitFor(() => expect(translate).toHaveBeenCalledWith({ body: { text: 'मीरा को 4 बजे बुक करो', mode: 'translate' } }));
        await waitFor(() => expect(box.value).toBe('Book Meera at 4'));
    });

    it('Roman script keeps the words', async () => {
        translate.mockResolvedValue({ data: { text: 'meera ko 4 baje book karo' } });
        composer();
        fireEvent.change(screen.getByRole('textbox'), { target: { value: 'मीरा को 4 बजे बुक करो' } });
        fireEvent.click(screen.getByRole('button', { name: 'Roman script' }));
        await waitFor(() => expect(translate.mock.calls[0][0].body.mode).toBe('transliterate'));
    });
});

describe('speaking instead of typing', () => {
    it('offers a microphone, and says so when the browser cannot record', async () => {
        composer();
        const mic = screen.getByRole('button', { name: 'Speak' });
        fireEvent.click(mic);
        // jsdom has no MediaRecorder: the honest answer, not a silent button.
        expect(await screen.findByRole('alert')).toBeTruthy();
        expect(screen.getByRole('alert').textContent).toContain('cannot record audio');
        expect(transcribe).not.toHaveBeenCalled();
    });
});
