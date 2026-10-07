/**
 * Screen 06: the helper picker. All five helpers and the builder reach it
 * from Chat; Automatic is the default; only an available helper can be
 * used; a helper that needs setup says why and sets up in this
 * conversation; a chosen helper rides on the message as a removable chip.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { afterEach, beforeEach, describe, expect, it, vi } from 'vitest';

import { ChannelComposer } from '@/components/channel/ChannelComposer';

import { HelperPicker } from '../HelperPicker';

const sdk = vi.hoisted(() => ({
    list: vi.fn(),
    setup: vi.fn(),
    post: vi.fn(),
}));
vi.mock('@/client/sdk.gen', () => ({
    listHelpersApiV1HelpersGet: sdk.list,
    setupHelperApiV1HelpersKeySetupPost: sdk.setup,
    postMessageApiV1TimelineMessagePost: sdk.post,
    translateTextApiV1TranslatePost: vi.fn(),
    transcribeAudioApiV1WorkflowRecordingsTranscribePost: vi.fn(),
    listFoldersApiV1FolderGet: async () => ({ data: [] }),
    brainsApiV1TimelineBrainsGet: async () => ({ data: { presets: [], vendors: [] } }),
    memoryApiV1TimelineMemoryGet: async () => ({ data: null }),
    myInterestsApiV1HelpersResearchInterestsGet: async () => ({ data: { interests: [], revision: 0, notice: 'Information only.' } }),
}));
const flags = vi.hoisted(() => ({ on: new Set<string>(['launch_helpers']) }));
vi.mock('@/lib/features', () => ({ useFeature: (name: string) => flags.on.has(name) }));

function helper(key: string, name: string, state = 'available', extra: Record<string, unknown> = {}) {
    return {
        key,
        name,
        job: `${name} job`,
        evidence: 'e',
        boundary: `${name} promise`,
        example: `${name} example`,
        permissions: [`${name} reads`],
        connections: [],
        templates: [],
        state,
        reason: state === 'available' ? null : `${name} reason`,
        setup: null,
        notes: [],
        advanced: null,
        ...extra,
    };
}

const FIVE = [
    helper('inbox', 'Inbox', 'needs_setup', {
        setup: { kind: 'connect', label: 'Connect Gmail', app: 'gmail' },
        connections: ['gmail', 'outlook'],
    }),
    helper('research', 'Research'),
    helper('follow_up', 'Follow-up'),
    helper('learning_guide', 'Learning Guide', 'disabled_by_policy'),
    helper('call_appointment', 'Call and Appointment', 'unavailable'),
];

beforeEach(() => {
    sdk.list.mockReset();
    sdk.setup.mockReset();
    sdk.post.mockReset();
    sdk.list.mockResolvedValue({
        data: { helpers: FIVE, builder: helper('builder', 'Build something'), can_manage: false },
    });
    sdk.post.mockResolvedValue({ data: { asked: [], unknown: [], ambiguous: [] } });
    flags.on = new Set(['launch_helpers']);
    window.innerWidth = 1280;
    // jsdom has no matchMedia; the picker reads the width the way the app does.
    window.matchMedia = ((query: string) => ({
        matches: false,
        media: query,
        addEventListener: () => undefined,
        removeEventListener: () => undefined,
    })) as unknown as typeof window.matchMedia;
    localStorage.clear();
});
afterEach(() => {
    window.innerWidth = 1024;
});

function picker(props: Partial<React.ComponentProps<typeof HelperPicker>> = {}) {
    const onChoose = vi.fn();
    const onExample = vi.fn();
    render(<HelperPicker selected={null} onChoose={onChoose} onExample={onExample} threadId="t-1" {...props} />);
    return { onChoose, onExample };
}

describe('the picker', () => {
    it('lists Automatic, all five helpers and the builder', async () => {
        picker();
        fireEvent.click(screen.getByTestId('helper-picker'));
        expect(await screen.findByTestId('helper-row-automatic')).toBeTruthy();
        for (const key of ['inbox', 'research', 'follow_up', 'learning_guide', 'call_appointment', 'builder']) {
            expect(screen.getByTestId(`helper-row-${key}`)).toBeTruthy();
        }
        // States are said with words, not only colour.
        expect(screen.getByTestId('helper-state-inbox').textContent).toContain('Needs setup');
        expect(screen.getByTestId('helper-state-learning_guide').textContent).toContain('Turned off by your workspace');
        expect(screen.getByTestId('helper-state-call_appointment').textContent).toContain('Unavailable');
    });

    it('opens a detail with what it does, its permissions and one example, then uses it', async () => {
        const { onChoose } = picker();
        fireEvent.click(screen.getByTestId('helper-picker'));
        fireEvent.click(await screen.findByTestId('helper-row-research'));
        expect(screen.getByText('Research job')).toBeTruthy();
        expect(screen.getByText('Research reads')).toBeTruthy();
        expect(screen.getByText('Research promise')).toBeTruthy();
        expect(screen.getByText(/Research example/)).toBeTruthy();
        fireEvent.click(screen.getByTestId('use-helper'));
        expect(onChoose).toHaveBeenCalledWith({ key: 'research', name: 'Research' });
    });

    it('a helper that is not available cannot be used and says why', async () => {
        picker();
        fireEvent.click(screen.getByTestId('helper-picker'));
        fireEvent.click(await screen.findByTestId('helper-row-learning_guide'));
        expect(screen.getByText('Learning Guide reason')).toBeTruthy();
        expect((screen.getByTestId('use-helper') as HTMLButtonElement).disabled).toBe(true);
    });

    it('sets up in this conversation and says the draft is kept', async () => {
        sdk.setup.mockResolvedValue({ data: { status: 'offered', note: 'x' } });
        picker();
        fireEvent.click(screen.getByTestId('helper-picker'));
        fireEvent.click(await screen.findByTestId('helper-row-inbox'));
        fireEvent.click(screen.getByRole('button', { name: 'Connect Gmail' }));
        await waitFor(() => expect(sdk.setup).toHaveBeenCalled());
        expect(sdk.setup.mock.calls[0][0]).toEqual({ path: { key: 'inbox' }, body: { thread_id: 't-1' } });
        expect(await screen.findByText(/A connect card is in this conversation/)).toBeTruthy();
    });

    it('a failed load is a failure with Retry, never an empty list', async () => {
        sdk.list.mockResolvedValueOnce({ error: { detail: 'boom' } });
        picker();
        fireEvent.click(screen.getByTestId('helper-picker'));
        expect(await screen.findByText('Helpers did not load')).toBeTruthy();
        fireEvent.click(screen.getByRole('button', { name: /Try again|Retry/ }));
        expect(await screen.findByTestId('helper-row-research')).toBeTruthy();
    });

    it('chooses a helper named on the address once the server says it is ready', async () => {
        const { onChoose } = picker({ initialKey: 'builder' });
        await waitFor(() => expect(onChoose).toHaveBeenCalledWith({ key: 'builder', name: 'Build something' }));
    });

    it('is a full-height sheet with a fixed Use footer on a phone', async () => {
        window.innerWidth = 375;
        picker();
        fireEvent.click(await screen.findByTestId('helper-picker'));
        fireEvent.click(await screen.findByTestId('helper-row-follow_up'));
        const dialog = screen.getByRole('dialog');
        expect(dialog.className).toContain('h-[100dvh]');
        expect(screen.getByTestId('use-helper')).toBeTruthy();
    });
});

describe('in the composer', () => {
    function composer() {
        return render(<ChannelComposer assistant threadId="t-1" bots={[]} channelName="Decibyl" chatShell />);
    }

    it('sends the chosen helper with the message, as a removable chip', async () => {
        composer();
        fireEvent.click(screen.getByTestId('helper-picker'));
        fireEvent.click(await screen.findByTestId('helper-row-research'));
        fireEvent.click(screen.getByTestId('use-helper'));
        expect(await screen.findByTestId('helper-chip')).toBeTruthy();
        fireEvent.change(screen.getByRole('textbox'), { target: { value: 'GST for a bakery?' } });
        fireEvent.click(screen.getByRole('button', { name: 'Send' }));
        await waitFor(() => expect(sdk.post).toHaveBeenCalled());
        expect(sdk.post.mock.calls[0][0].body.helper).toBe('research');
    });

    it('removing the chip goes back to Automatic: no helper is sent', async () => {
        composer();
        fireEvent.click(screen.getByTestId('helper-picker'));
        fireEvent.click(await screen.findByTestId('helper-row-research'));
        fireEvent.click(screen.getByTestId('use-helper'));
        fireEvent.click(await screen.findByRole('button', { name: /Remove Research/ }));
        expect(screen.queryByTestId('helper-chip')).toBeNull();
        fireEvent.change(screen.getByRole('textbox'), { target: { value: 'hello' } });
        fireEvent.click(screen.getByRole('button', { name: 'Send' }));
        await waitFor(() => expect(sdk.post).toHaveBeenCalled());
        expect(sdk.post.mock.calls[0][0].body.helper).toBeUndefined();
    });

    it('with the flag off there is no picker and the body is what it was', async () => {
        flags.on = new Set();
        composer();
        expect(screen.queryByTestId('helper-picker')).toBeNull();
        fireEvent.change(screen.getByRole('textbox'), { target: { value: 'hello' } });
        fireEvent.click(screen.getByRole('button', { name: 'Send' }));
        await waitFor(() => expect(sdk.post).toHaveBeenCalled());
        expect('helper' in sdk.post.mock.calls[0][0].body).toBe(false);
    });

    it('a bot chat never shows the picker', () => {
        render(<ChannelComposer workflowId={3} bots={[]} channelName="Bot" chatShell />);
        expect(screen.queryByTestId('helper-picker')).toBeNull();
    });
});
