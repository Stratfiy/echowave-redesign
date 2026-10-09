/** "Call me when long tasks finish": hidden while off; on, read and saved
 *  against the revision it read. */
import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const read = vi.hoisted(() => vi.fn());
const save = vi.hoisted(() => vi.fn());
const flags = vi.hoisted(() => ({ call_when_done: true, member_preferences: true }));
vi.mock('@/client/sdk.gen', () => ({
    myPreferencesApiV1MePreferencesGet: read,
    saveMyPreferencesApiV1MePreferencesPut: save,
}));
vi.mock('@/lib/auth', () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock('@/lib/features', () => ({
    useFeature: (name: string) => Boolean((flags as Record<string, boolean>)[name]),
}));

import { CallWhenDoneSetting } from '../CallWhenDoneSetting';

beforeEach(() => {
    read.mockReset();
    save.mockReset();
    flags.call_when_done = true;
    read.mockResolvedValue({ data: { call_when_done: null, revision: 3 } });
});

describe('CallWhenDoneSetting', () => {
    it('shows nothing while the flag is off', () => {
        flags.call_when_done = false;
        const { container } = render(<CallWhenDoneSetting />);
        expect(container.innerHTML).toBe('');
        expect(read).not.toHaveBeenCalled();
    });

    it('turns the standing preference on', async () => {
        save.mockResolvedValue({ data: { call_when_done: true, revision: 4 } });
        render(<CallWhenDoneSetting />);
        const toggle = await screen.findByRole('switch', { name: 'Call me when long tasks finish' });
        await waitFor(() => expect(toggle.hasAttribute('disabled')).toBe(false));
        fireEvent.click(toggle);
        await waitFor(() => expect(save).toHaveBeenCalledWith({ body: { call_when_done: true, revision: 3 } }));
        await waitFor(() => expect(toggle.getAttribute('aria-checked')).toBe('true'));
    });

    it('says why a save failed', async () => {
        save.mockResolvedValue({ error: { detail: 'These were changed somewhere else.' } });
        render(<CallWhenDoneSetting />);
        const toggle = await screen.findByRole('switch', { name: 'Call me when long tasks finish' });
        await waitFor(() => expect(toggle.hasAttribute('disabled')).toBe(false));
        fireEvent.click(toggle);
        expect((await screen.findByRole('alert')).textContent).toContain('These were changed somewhere else.');
    });
});
