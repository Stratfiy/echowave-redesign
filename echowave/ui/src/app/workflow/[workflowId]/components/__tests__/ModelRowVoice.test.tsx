/**
 * The About-this-agent panel names the agent's voice. Production showed
 * Voice: "HBlqQDCBvQxsEK8OFtEZ" -- the provider's id for a library voice the
 * row could not find in its list, printed as if it were a name.
 */
import { fireEvent, render, screen } from '@testing-library/react';
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const get = vi.hoisted(() => vi.fn());
vi.mock('@/client/client.gen', () => ({ client: { get, post: vi.fn() } }));
vi.mock('@/lib/auth', () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));

type EditorProps = {
    applyNow?: boolean;
    onAppliedNow?: (result: { draft_kept?: boolean }) => void;
};
const editorProps = vi.hoisted(() => [] as EditorProps[]);
// The pencil's own behaviour is tested in ModelSlotEditor.test.tsx; here it
// only has to report that it went live.
vi.mock('../ModelSlotEditor', () => ({
    ModelSlotEditor: (props: EditorProps) => {
        editorProps.push(props);
        return (
            <button type="button" onClick={() => props.onAppliedNow?.({ draft_kept: true })}>
                Apply
            </button>
        );
    },
}));

import { ModelRow, voiceLabel } from '../ModelRow';

const LIBRARY_ID = 'HBlqQDCBvQxsEK8OFtEZ';

function row(voices: { voice_id: string; name: string; description: string | null }[]) {
    return {
        data: {
            is_realtime: false,
            slots: [
                {
                    component: 'tts',
                    title: 'Voice',
                    provider: 'elevenlabs',
                    model: 'eleven_flash_v2_5',
                    paise_per_minute: null,
                    approximate: false,
                    latency_ms: null,
                    voice: LIBRARY_ID,
                },
            ],
            voices: voices.map((v) => ({ ...v, gender: 'female', is_default: false, sample_url: null, sample_url_hi: null })),
            cost: {
                total_paise_per_minute: 0,
                agent_paise_per_minute: 0,
                telephony_paise_per_minute: 0,
                platform_paise_per_minute: 0,
                unpriced: [],
            },
            latency: null,
        },
    };
}

describe('the voice row', () => {
    beforeEach(() => get.mockReset());

    it("shows the voice's name and language, not its id", async () => {
        get.mockResolvedValue(row([{ voice_id: LIBRARY_ID, name: 'Priya', description: 'Hindi' }]));
        render(<ModelRow workflowId={46} voiceOnly />);
        const voice = await screen.findByTestId('voice-row');
        expect(voice.textContent).toContain('Priya · Hindi');
        expect(voice.textContent).not.toContain(LIBRARY_ID);
    });

    it('says "Custom voice" when nothing can name it', async () => {
        get.mockResolvedValue(row([{ voice_id: 'other', name: 'Rachel', description: null }]));
        render(<ModelRow workflowId={46} voiceOnly />);
        const voice = await screen.findByTestId('voice-row');
        expect(voice.textContent).toBe('Custom voice');
    });

    it('puts a change live and says the next call uses it', async () => {
        get.mockResolvedValue(row([{ voice_id: LIBRARY_ID, name: 'Priya', description: 'Hindi' }]));
        const onVoiceApplied = vi.fn();
        render(<ModelRow workflowId={46} voiceOnly editable onVoiceApplied={onVoiceApplied} />);
        await screen.findByTestId('voice-row');
        expect(editorProps.at(-1)?.applyNow).toBe(true);
        expect(screen.queryByTestId('voice-applied')).toBeNull();

        fireEvent.click(screen.getByRole('button', { name: 'Apply' }));

        const status = await screen.findByTestId('voice-applied');
        expect(status.textContent).toContain('Saved — next call uses this voice.');
        expect(status.textContent).toContain('still a draft');
        expect(onVoiceApplied).toHaveBeenCalled();
    });
});

describe('voiceLabel', () => {
    it('never falls back to the id', () => {
        expect(voiceLabel(LIBRARY_ID, undefined)).toBe('Custom voice');
        // A list that echoes the id back as the name has not named it.
        expect(
            voiceLabel(LIBRARY_ID, {
                voice_id: LIBRARY_ID,
                name: LIBRARY_ID,
                gender: null,
                description: null,
                is_default: false,
                sample_url: null,
                sample_url_hi: null,
            }),
        ).toBe('Custom voice');
        expect(voiceLabel(null, undefined)).toBe('Default voice');
    });
});
