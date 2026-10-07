/**
 * Segmented capture (screen 11): self-contained segments, a timer that
 * follows real capture, and interruptions reported as gaps with their
 * length -- never skipped.
 */

import { describe, expect, it } from 'vitest';

import { CaptureClock, type Gap, type Segment, SegmentedRecorder } from '../recorder';

class FakeRecorder {
    state = 'inactive';
    mimeType = 'audio/webm';
    ondataavailable: ((event: { data: Blob }) => void) | null = null;
    onstop: (() => void) | null = null;
    onerror: ((event: unknown) => void) | null = null;
    start() {
        this.state = 'recording';
    }
    stop() {
        this.state = 'inactive';
        this.ondataavailable?.({ data: new Blob(['x'], { type: 'audio/webm' }) });
        this.onstop?.();
    }
}

function rig() {
    let now = 0;
    const timers: Array<{ fn: () => void; at: number; id: number }> = [];
    let next = 1;
    const track: Record<string, (() => void) | null> = { onmute: null, onunmute: null, onended: null };
    const stream = { getAudioTracks: () => [track], getTracks: () => [track] } as unknown as MediaStream;
    const segments: Segment[] = [];
    const gaps: Gap[] = [];
    let hidden = false;
    const recorder = new SegmentedRecorder({
        stream,
        segmentMs: 25000,
        onSegment: (s) => segments.push(s),
        onGap: (g) => gaps.push(g),
        now: () => now,
        createRecorder: () => new FakeRecorder(),
        isHidden: () => hidden,
        setTimer: (fn, ms) => {
            const id = next++;
            timers.push({ fn, at: now + ms, id });
            return id;
        },
        clearTimer: (id) => {
            const index = timers.findIndex((t) => t.id === id);
            if (index >= 0) timers.splice(index, 1);
        },
    });
    const advance = (ms: number) => {
        const until = now + ms;
        while (true) {
            timers.sort((a, b) => a.at - b.at);
            const due = timers[0];
            if (!due || due.at > until) break;
            timers.shift();
            now = due.at;
            due.fn();
        }
        now = until;
    };
    return {
        recorder,
        segments,
        gaps,
        track,
        advance,
        hide: (value: boolean) => {
            hidden = value;
        },
    };
}

describe('CaptureClock', () => {
    it('counts only while running', () => {
        let now = 0;
        const clock = new CaptureClock(() => now);
        clock.run();
        now = 5000;
        clock.hold();
        now = 9000;
        expect(clock.elapsed()).toBe(5000);
        clock.run();
        now = 10000;
        expect(clock.elapsed()).toBe(6000);
    });
});

describe('SegmentedRecorder', () => {
    it('cuts a self-contained segment every 25 seconds, numbered in order', async () => {
        const r = rig();
        r.recorder.start();
        r.advance(60000);
        const done = await r.recorder.stop();
        expect(r.segments.map((s) => [s.seq, s.startMs, s.endMs])).toEqual([
            [0, 0, 25000],
            [1, 25000, 50000],
            [2, 50000, 60000],
        ]);
        expect(done).toEqual({ lastSeq: 2, capturedMs: 60000 });
    });

    it('a pause stops the clock and is not a gap', async () => {
        const r = rig();
        r.recorder.start();
        r.advance(10000);
        r.recorder.pause();
        r.advance(30000);
        r.recorder.resume();
        r.advance(5000);
        const done = await r.recorder.stop();
        expect(done.capturedMs).toBe(15000);
        expect(r.gaps).toEqual([]);
        expect(r.segments.map((s) => [s.startMs, s.endMs])).toEqual([
            [0, 10000],
            [10000, 15000],
        ]);
    });

    it('a muted microphone is a gap with its wall-clock length', async () => {
        const r = rig();
        r.recorder.start();
        r.advance(8000);
        r.track.onmute?.();
        expect(r.recorder.state).toBe('interrupted');
        r.advance(4000);
        r.track.onunmute?.();
        expect(r.recorder.state).toBe('recording');
        r.advance(2000);
        await r.recorder.stop();
        expect(r.gaps).toEqual([{ atMs: 8000, durationMs: 4000, reason: 'microphone_lost' }]);
        expect(r.recorder.clock.elapsed()).toBe(10000);
    });

    it('says when the page was in the background', () => {
        const r = rig();
        r.recorder.start();
        r.advance(1000);
        r.hide(true);
        r.track.onmute?.();
        r.advance(3000);
        r.track.onunmute?.();
        expect(r.gaps[0].reason).toBe('backgrounded');
    });

    it('an ended microphone waits for a new one, and the wait is a gap', async () => {
        const r = rig();
        r.recorder.start();
        r.advance(5000);
        r.track.onended?.();
        r.advance(7000);
        expect(r.recorder.state).toBe('interrupted');
        const track = { onmute: null, onunmute: null, onended: null };
        r.recorder.replaceStream({ getAudioTracks: () => [track], getTracks: () => [track] } as unknown as MediaStream);
        expect(r.recorder.state).toBe('recording');
        r.advance(1000);
        const done = await r.recorder.stop();
        expect(r.gaps).toEqual([{ atMs: 5000, durationMs: 7000, reason: 'microphone_lost' }]);
        expect(done.capturedMs).toBe(6000);
    });

    it('stopping while interrupted still reports the gap', async () => {
        const r = rig();
        r.recorder.start();
        r.advance(3000);
        r.track.onended?.();
        r.advance(2000);
        await r.recorder.stop();
        expect(r.gaps).toHaveLength(1);
        expect(r.segments).toHaveLength(1);
    });
});
