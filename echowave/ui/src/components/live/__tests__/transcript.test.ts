/**
 * The listen panel's transcript: lines grow in place, the backlog and the
 * live events merge into the call's own order, and nothing is applied twice.
 */
import { describe, expect, it } from 'vitest';

import { conversationItemsFromRealtimeFeedbackEvents } from '@/components/workflow/conversation/adapters/fromRealtimeFeedback';

import { apply, applyAll, duration, EMPTY, type LiveEvent } from '../transcript';

const line = (seq: number, line: number, speaker: 'caller' | 'agent', text: string, final = false): LiveEvent => ({
    type: 'line',
    seq,
    line,
    speaker,
    text,
    final,
});

describe('the transcript', () => {
    it('grows a line in place and keeps the order the call had', () => {
        const state = applyAll(EMPTY, [
            line(1, 1, 'agent', 'Hello,'),
            line(2, 1, 'agent', 'Hello, how can I help?', true),
            line(3, 2, 'caller', 'I want'),
            line(4, 2, 'caller', 'I want to book', true),
        ]);
        expect(state.entries.map((e) => (e.kind === 'line' ? [e.speaker, e.text, e.final] : null))).toEqual([
            ['agent', 'Hello, how can I help?', true],
            ['caller', 'I want to book', true],
        ]);
    });

    it('merges a backlog with live events, each applied once', () => {
        const live = [line(5, 3, 'agent', 'Sure'), line(4, 2, 'caller', 'Tomorrow', true)];
        const backlog = [line(2, 1, 'agent', 'Hi there', true), line(4, 2, 'caller', 'Tomorrow', true)];
        const state = applyAll(applyAll(EMPTY, live), backlog);
        expect(state.entries.map((e) => e.key)).toEqual(['line-1', 'line-2', 'line-3']);
        expect(state.seen.size).toBe(3);
    });

    it('never lets a late interim overwrite a final line', () => {
        const state = applyAll(EMPTY, [line(2, 1, 'caller', 'I want to book', true), line(1, 1, 'caller', 'I want')]);
        const [entry] = state.entries;
        expect(entry.kind === 'line' && entry.text).toBe('I want to book');
    });

    it('shows a whisper as its own entry, and knows when the call ends', () => {
        let state = apply(EMPTY, { type: 'whisper', seq: 7, id: 'w1', by: 'priya', text: 'Offer 5 pm', urgent: true });
        state = apply(state, { type: 'step', seq: 8, step: 'Booking' });
        state = apply(state, { type: 'ended', seq: 9 });
        expect(state.entries[0]).toMatchObject({ kind: 'whisper', by: 'priya', text: 'Offer 5 pm', urgent: true });
        expect(state.step).toBe('Booking');
        expect(state.ended).toBe(true);
    });

    it('reads a duration the way a clock does', () => {
        expect(duration(7)).toBe('0:07');
        expect(duration(187)).toBe('3:07');
        expect(duration(3765)).toBe('1:02:45');
    });
});

describe("a whisper on the call's record afterwards", () => {
    it('is shown where it happened, marked as not spoken', () => {
        const items = conversationItemsFromRealtimeFeedbackEvents([
            { type: 'rtf-bot-text', payload: { text: 'How can I help?' }, timestamp: 't1', turn: 1 },
            {
                type: 'rtf-supervisor-whisper',
                payload: { text: 'Offer the 5 pm slot', by: 'priya', urgent: false },
                timestamp: 't2',
                turn: 1,
            },
        ]);
        expect(items[1]).toMatchObject({
            kind: 'notice',
            tone: 'info',
            title: 'Whisper from priya · not spoken to the caller',
            text: 'Offer the 5 pm slot',
        });
        // Not a line the agent said.
        expect(items.filter((i) => i.kind === 'message')).toHaveLength(1);
    });
});
