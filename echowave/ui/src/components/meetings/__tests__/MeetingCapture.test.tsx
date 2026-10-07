/**
 * Screen 11: three ways in, each available or needing setup before anything
 * is captured; consent before audio; the source stated as exactly what it is.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

import type { MeetingCapabilities } from '@/client/types.gen';

import { MeetingCapture, parseParticipants } from '../MeetingCapture';
import { record } from './fixtures';

const api = vi.hoisted(() => ({ caps: vi.fn(), create: vi.fn(), upload: vi.fn() }));
vi.mock('@/client/sdk.gen', () => ({
    meetingCapabilitiesApiV1MeetingsCapabilitiesGet: api.caps,
    createMeetingApiV1MeetingsPost: api.create,
    uploadRecordingApiV1MeetingsMeetingIdUploadPost: api.upload,
    addSegmentApiV1MeetingsMeetingIdSegmentsPost: vi.fn(),
    getMeetingApiV1MeetingsMeetingIdGet: vi.fn(),
    pauseMeetingApiV1MeetingsMeetingIdPausePost: vi.fn(),
    reportGapApiV1MeetingsMeetingIdGapsPost: vi.fn(),
    resumeMeetingApiV1MeetingsMeetingIdResumePost: vi.fn(),
    stopMeetingApiV1MeetingsMeetingIdStopPost: vi.fn(),
}));
vi.mock('@/lib/auth', () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
const push = vi.hoisted(() => vi.fn());
vi.mock('next/navigation', () => ({ useRouter: () => ({ push }) }));

// Radix measures with ResizeObserver, which jsdom does not have.
globalThis.ResizeObserver ??= class {
    observe() {}
    unobserve() {}
    disconnect() {}
} as unknown as typeof ResizeObserver;

function caps(overrides: Partial<MeetingCapabilities> = {}): MeetingCapabilities {
    const ok = { state: 'available', reason: null };
    return {
        sources: { microphone: ok, upload: ok, notes: ok },
        transcription: { ...ok, provider: 'sarvam', key_source: 'platform' },
        summary: ok,
        languages: [
            { code: 'unknown', native: 'Detect automatically', english: 'Detect' },
            { code: 'hi', native: 'हिन्दी', english: 'Hindi' },
        ],
        max_upload_mb: 50,
        max_minutes: 120,
        segment_seconds: 25,
        limits_note: "Records what this device's microphone hears. It cannot record another app's audio or a phone call.",
        retention_note: 'Audio is not kept.',
        ...overrides,
    };
}

beforeEach(() => {
    for (const fn of Object.values(api)) fn.mockReset();
    push.mockReset();
});

describe('meeting capture', () => {
    it('offers the three ways in and says what it cannot record', async () => {
        api.caps.mockResolvedValue({ data: caps() });
        render(<MeetingCapture threadId="t-1" />);
        expect(await screen.findByText('Record this meeting')).toBeTruthy();
        expect(screen.getByText('Upload a recording')).toBeTruthy();
        expect(screen.getByText('Paste notes')).toBeTruthy();
        expect(screen.getByText(/cannot record another app's audio or a phone call/)).toBeTruthy();
    });

    it('shows needs setup, with the reason, instead of a dead button', async () => {
        const setup = { state: 'needs_setup', reason: 'Transcription needs setup: no Sarvam speech-to-text key is configured.' };
        api.caps.mockResolvedValue({ data: caps({ sources: { microphone: setup, upload: setup, notes: { state: 'available', reason: null } } }) });
        render(<MeetingCapture threadId={null} />);
        const record = await screen.findByTestId('choice-microphone');
        expect((record as HTMLButtonElement).disabled).toBe(true);
        expect(record.textContent).toContain('Transcription needs setup: no Sarvam');
        expect(record.textContent).not.toContain('Needs setup: Transcription');
        expect((screen.getByTestId('choice-notes') as HTMLButtonElement).disabled).toBe(false);
    });

    it('recording needs consent first, and states the source', async () => {
        api.caps.mockResolvedValue({ data: caps() });
        render(<MeetingCapture threadId="t-1" />);
        fireEvent.click(await screen.findByTestId('choice-microphone'));
        expect(screen.getByText(/Audio source:/).textContent).toContain("This device's microphone");
        const start = screen.getByRole('button', { name: 'Start recording' }) as HTMLButtonElement;
        expect(start.disabled).toBe(true);
        fireEvent.click(screen.getByLabelText(/Everyone in this meeting has agreed/));
        expect(start.disabled).toBe(false);
    });

    it('a refused microphone is said, and nothing is created', async () => {
        api.caps.mockResolvedValue({ data: caps() });
        Object.defineProperty(window, 'MediaRecorder', { value: class {}, configurable: true });
        Object.defineProperty(navigator, 'mediaDevices', {
            value: { getUserMedia: vi.fn().mockRejectedValue(new Error('denied')) },
            configurable: true,
        });
        render(<MeetingCapture threadId="t-1" />);
        fireEvent.click(await screen.findByTestId('choice-microphone'));
        fireEvent.click(screen.getByLabelText(/Everyone in this meeting has agreed/));
        fireEvent.click(screen.getByRole('button', { name: 'Start recording' }));
        expect(await screen.findByText(/Microphone access was refused/)).toBeTruthy();
        expect(api.create).not.toHaveBeenCalled();
    });

    it('pasted notes need no consent and open the record', async () => {
        api.caps.mockResolvedValue({ data: caps() });
        api.create.mockResolvedValue({ data: record({ id: 'n1', source: 'notes' }) });
        render(<MeetingCapture threadId="t-1" />);
        fireEvent.click(await screen.findByTestId('choice-notes'));
        expect(screen.queryByLabelText(/Everyone in this meeting has agreed/)).toBeNull();
        fireEvent.change(screen.getByLabelText('Notes'), { target: { value: 'I will send the deck.' } });
        fireEvent.change(screen.getByLabelText(/Participants/), { target: { value: 'Priya, Ravi' } });
        fireEvent.click(screen.getByRole('button', { name: 'Save notes' }));
        await waitFor(() => expect(push).toHaveBeenCalledWith('/meetings/n1'));
        const body = api.create.mock.calls[0][0].body;
        expect(body).toMatchObject({ source: 'notes', consent_confirmed: false, origin_thread_id: 't-1', participants: ['Priya', 'Ravi'] });
    });

    it('a recording over the size limit is refused before upload', async () => {
        api.caps.mockResolvedValue({ data: caps({ max_upload_mb: 1 }) });
        render(<MeetingCapture threadId={null} />);
        fireEvent.click(await screen.findByTestId('choice-upload'));
        const input = document.querySelector('input[type=file]') as HTMLInputElement;
        const big = new File([new Uint8Array(2 * 1024 * 1024)], 'call.mp3', { type: 'audio/mpeg' });
        fireEvent.change(input, { target: { files: [big] } });
        expect(screen.getByTestId('upload-file').textContent).toContain('call.mp3');
        fireEvent.click(screen.getByLabelText(/Everyone in this meeting has agreed/));
        fireEvent.click(screen.getByRole('button', { name: 'Upload and transcribe' }));
        expect(await screen.findByText('That file is larger than 1 MB.')).toBeTruthy();
        expect(api.create).not.toHaveBeenCalled();
    });

    it('a capability check that fails is an error with retry, not an empty screen', async () => {
        api.caps.mockResolvedValue({ error: { detail: 'down' }, response: { status: 500 } });
        render(<MeetingCapture threadId={null} />);
        expect(await screen.findByText('Could not check what is set up')).toBeTruthy();
        expect(screen.getByRole('button', { name: 'Try again' })).toBeTruthy();
    });

    it('participants are split on commas and lines', () => {
        expect(parseParticipants('Priya, Ravi\nMeera,,')).toEqual(['Priya', 'Ravi', 'Meera']);
    });
});
