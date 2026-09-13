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
vi.mock('@/client/sdk.gen', () => ({
    postMessageApiV1TimelineMessagePost: post,
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
                body: { folder_id: 9, text: '@front are we open?' },
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
