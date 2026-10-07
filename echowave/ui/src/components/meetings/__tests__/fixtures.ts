import type { MeetingAction, MeetingRecord } from '@/client/types.gen';

export function action(overrides: Partial<MeetingAction> = {}): MeetingAction {
    return {
        id: 11,
        kind: 'action',
        text: 'Send the deck to the client',
        owner_name: 'Priya',
        due_text: 'by Friday',
        due_at: '2026-10-09T11:30:00+00:00',
        confidence: 'high',
        missing: [],
        segment_seq: 0,
        excerpt: 'I will send the deck by Friday',
        source_found: true,
        edited: false,
        task_id: null,
        card: null,
        ...overrides,
    };
}

export function record(overrides: Partial<MeetingRecord> = {}): MeetingRecord {
    return {
        id: 'm1',
        title: 'Launch sync',
        source: 'microphone',
        source_label: "This device's microphone",
        language: 'hi',
        participants: ['Priya', 'Ravi'],
        status: 'ready',
        status_reason: null,
        captured_ms: 65000,
        created_at: '2026-10-07T10:00:00+00:00',
        capture_started_at: null,
        capture_ended_at: null,
        consent_confirmed_at: '2026-10-07T10:00:00+00:00',
        origin_thread_id: 't-9',
        upload_name: null,
        revision: 3,
        reading_status: 'ready',
        reading_note: null,
        summary: ['Priya will send the deck.'],
        transcript: [
            { seq: 0, start_ms: 0, end_ms: 25000, status: 'done', text: 'I will send the deck by Friday', original_text: null, corrected: false, error: null, has_action_cue: true },
            { seq: 2, start_ms: 40000, end_ms: 65000, status: 'failed', text: '', original_text: null, corrected: false, error: 'Sarvam failed.', has_action_cue: false },
        ],
        breaks: [{ kind: 'gap', reason: 'missing_segment', reason_label: 'This part never arrived', at_ms: 25000, duration_ms: null }],
        decisions: [],
        actions: [action()],
        possible_actions: [],
        ...overrides,
    };
}
