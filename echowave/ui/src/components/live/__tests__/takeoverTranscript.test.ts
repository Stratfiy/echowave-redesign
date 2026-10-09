/**
 * Who has the call, in the listen panel's transcript and on the call's own
 * record; the supervisor's side of the audio; the microphone's wire format.
 */
import { describe, expect, it } from 'vitest';

import { conversationItemsFromRealtimeFeedbackEvents } from '@/components/workflow/conversation/adapters/fromRealtimeFeedback';

import { LivePlayer, parsePacket } from '../liveAudio';
import { downsample, encodeMic, levelOf, MIC_RATE } from '../talkAudio';
import { AGENT_HAS_IT, applyAll, EMPTY, escalationText, type LiveEvent, takeoverText } from '../transcript';

const takeover = (seq: number, action: string, over: Partial<LiveEvent> = {}): LiveEvent => ({
    type: 'takeover',
    seq,
    action,
    mode: 'barge',
    by: 'priya',
    by_user_id: 7,
    ...over,
});

describe('who has the call', () => {
    it('follows joins, switches, letting the agent answer and the hand-back', () => {
        let state = applyAll(EMPTY, [takeover(1, 'joined')]);
        expect(state.takeover).toEqual({ mode: 'barge', by: 'priya', byUserId: 7, agentAnswering: false });
        state = applyAll(state, [takeover(2, 'agent_answering')]);
        expect(state.takeover.agentAnswering).toBe(true);
        state = applyAll(state, [takeover(3, 'agent_paused')]);
        expect(state.takeover.agentAnswering).toBe(false);
        state = applyAll(state, [takeover(4, 'switched', { mode: 'takeover' })]);
        expect(state.takeover.mode).toBe('takeover');
        state = applyAll(state, [takeover(5, 'handed_back', { mode: 'ai' })]);
        expect(state.takeover).toEqual(AGENT_HAS_IT);
        expect(state.entries.map((e) => (e.kind === 'takeover' ? e.text : null))).toEqual([
            'priya joined the call. The agent is paused.',
            'priya let the agent answer.',
            'The agent stopped as priya spoke.',
            'priya took over the call. The agent is silent until it is handed back.',
            'priya handed the call back to the agent.',
        ]);
    });

    it('a recovery and a failed join both leave the agent with the call', () => {
        const recovered = applyAll(EMPTY, [
            takeover(1, 'joined', { mode: 'takeover' }),
            takeover(2, 'recovered', { mode: 'ai', supervisor: 'priya' }),
        ]);
        expect(recovered.takeover).toEqual(AGENT_HAS_IT);
        expect(takeoverText({ action: 'recovered', supervisor: 'priya', by: 'priya' })).toBe(
            'priya dropped off the call. The agent took it back.',
        );
        const failed = applyAll(EMPTY, [takeover(1, 'failed', { mode: 'ai', detail: 'This call is not on Plivo.' })]);
        expect(failed.takeover).toEqual(AGENT_HAS_IT);
        expect(failed.entries[0].kind === 'takeover' && failed.entries[0].text).toBe(
            'priya could not join the call. This call is not on Plivo.',
        );
    });

    it('a stretch of the supervisor speaking is one entry, finished in place', () => {
        const state = applyAll(EMPTY, [
            { type: 'line', seq: 1, line: 1, speaker: 'caller', text: 'Hello?', final: true },
            { type: 'supervisor', seq: 2, id: 'sp1', by: 'priya', final: false, seconds: 0 },
            { type: 'line', seq: 3, line: 2, speaker: 'caller', text: 'Oh, hi', final: true },
            { type: 'supervisor', seq: 4, id: 'sp1', by: 'priya', final: true, seconds: 12 },
            // A late interim never undoes the final one.
            { type: 'supervisor', seq: 5, id: 'sp1', by: 'priya', final: false, seconds: 0 },
        ]);
        expect(state.entries.map((e) => e.key)).toEqual(['line-1', 'supervisor-sp1', 'line-2']);
        const span = state.entries[1];
        expect(span.kind === 'supervisor' && [span.final, span.seconds]).toEqual([true, 12]);
    });
});

describe('the call record', () => {
    it('shows joins, hand-backs and the supervisor speaking as notices', () => {
        const items = conversationItemsFromRealtimeFeedbackEvents([
            {
                type: 'rtf-supervisor-takeover',
                payload: { action: 'joined', mode: 'takeover', by: 'priya' },
                timestamp: '2026-10-09T06:00:00Z',
                turn: 2,
            },
            {
                type: 'rtf-supervisor-speech',
                payload: { by: 'priya', seconds: 75 },
                timestamp: '2026-10-09T06:00:30Z',
                turn: 2,
            },
            {
                type: 'rtf-supervisor-takeover',
                payload: { action: 'handed_back', mode: 'ai', by: 'priya' },
                timestamp: '2026-10-09T06:02:00Z',
                turn: 3,
            },
        ]);
        expect(items.map((i) => (i.kind === 'notice' ? [i.title, i.text, i.icon] : i.kind))).toEqual([
            ['Supervisor', 'priya took over the call. The agent is silent until it is handed back.', 'user'],
            ['priya (supervisor)', 'Spoke to the caller · 1:15 · not transcribed', 'user'],
            ['Supervisor', 'priya handed the call back to the agent.', 'user'],
        ]);
    });
});

describe('audio', () => {
    it('reads the supervisor as their own side, and the one speaking skips it', () => {
        const pcm = new Int16Array([1000, -1000]);
        const buffer = new ArrayBuffer(6 + pcm.byteLength);
        const view = new DataView(buffer);
        view.setUint8(0, 's'.charCodeAt(0));
        view.setUint32(1, 16000, true);
        view.setUint8(5, 1);
        new Int16Array(buffer, 6).set(pcm);
        const packet = parsePacket(buffer);
        expect(packet?.side).toBe('supervisor');

        let started = 0;
        const context = {
            currentTime: 0,
            destination: {},
            createBuffer: () => ({ getChannelData: () => new Float32Array(2), duration: 0.001 }),
            createBufferSource: () => ({ connect() {}, start: () => (started += 1), stop() {} }),
        } as unknown as AudioContext;
        const player = new LivePlayer(context);
        player.play(packet!);
        expect(started).toBe(1);
        player.skipSupervisor = true;
        player.play(packet!);
        expect(started).toBe(1);
    });

    it('sends the microphone as side "s", mono, 16 kHz', () => {
        const slice = downsample(new Float32Array(4800).fill(0.5), 48000, MIC_RATE);
        expect(slice.length).toBe(1600);
        const view = new DataView(encodeMic(slice));
        expect(String.fromCharCode(view.getUint8(0))).toBe('s');
        expect(view.getUint32(1, true)).toBe(16000);
        expect(view.getUint8(5)).toBe(1);
        expect(view.getInt16(6, true)).toBe(16383);
        expect(view.byteLength).toBe(6 + 1600 * 2);
        expect(downsample(slice, 16000, 16000)).toBe(slice);
        expect(levelOf(new Float32Array(10))).toBe(0);
        expect(levelOf(new Float32Array(10).fill(0.5))).toBe(1);
    });
});

describe('the supervisor’s words, and escalations held back', () => {
    it('shows the supervisor’s transcribed words under their name, late words included', () => {
        const state = applyAll(EMPTY, [
            { type: 'supervisor', seq: 1, id: 'sp1', by: 'priya', final: false, seconds: 0 },
            { type: 'supervisor', seq: 2, id: 'sp1', by: 'priya', final: false, seconds: 0, text: 'Let me' },
            { type: 'supervisor', seq: 3, id: 'sp1', by: 'priya', final: true, seconds: 3 },
            // The last words, after the stretch ended.
            { type: 'supervisor', seq: 4, id: 'sp1', by: 'priya', final: true, seconds: 3, text: 'Let me check that.' },
        ]);
        expect(state.entries).toHaveLength(1);
        const span = state.entries[0];
        expect(span.kind === 'supervisor' && [span.by, span.text, span.final, span.seconds]).toEqual([
            'priya',
            'Let me check that.',
            true,
            3,
        ]);
    });

    it('keeps words already heard when a later event has none', () => {
        const state = applyAll(EMPTY, [
            { type: 'supervisor', seq: 1, id: 'sp1', by: 'priya', final: false, seconds: 0, text: 'One moment' },
            { type: 'supervisor', seq: 2, id: 'sp1', by: 'priya', final: true, seconds: 2 },
        ]);
        const span = state.entries[0];
        expect(span.kind === 'supervisor' && span.text).toBe('One moment');
    });

    it('says what escalation held back while a supervisor had the call', () => {
        const state = applyAll(EMPTY, [
            { type: 'escalation', seq: 1, label: 'Caller asked for a manager', supervisor: 'priya' },
        ]);
        expect(state.entries).toEqual([
            {
                kind: 'escalation',
                key: 'escalation-1',
                seq: 1,
                text: 'Caller asked for a manager. Not transferred: priya is on the call.',
            },
        ]);
        expect(escalationText({})).toBe('The caller wanted a person. Not transferred: a supervisor is on the call.');
    });

    it('puts both on the call record: one item per stretch, and the held-back escalation', () => {
        const items = conversationItemsFromRealtimeFeedbackEvents([
            {
                type: 'rtf-supervisor-speech',
                payload: { id: 'sp1', by: 'priya', seconds: 4, transcribed: false },
                timestamp: '2026-10-09T06:00:30Z',
                turn: 2,
            },
            {
                type: 'rtf-escalation-suppressed',
                payload: { label: 'Caller asked for a manager', supervisor: 'priya' },
                timestamp: '2026-10-09T06:00:31Z',
                turn: 2,
            },
            {
                type: 'rtf-supervisor-speech',
                payload: { id: 'sp1', by: 'priya', seconds: 4, transcribed: true, text: 'I can help with that.' },
                timestamp: '2026-10-09T06:00:32Z',
                turn: 2,
            },
        ]);
        expect(items.map((i) => (i.kind === 'notice' ? [i.title, i.text, i.tone] : i.kind))).toEqual([
            ['priya (supervisor)', 'I can help with that.', 'info'],
            ['Escalation held back', 'Caller asked for a manager. Not transferred: priya is on the call.', 'warning'],
        ]);
    });
});
