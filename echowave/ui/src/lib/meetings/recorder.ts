"use client";

/**
 * Meeting capture on this device's microphone, in short self-contained
 * segments (screen 11).
 *
 * Why segments: Sarvam's synchronous speech-to-text takes about thirty
 * seconds of audio, a live transcript needs words while the meeting is still
 * going, and a dropped connection should lose one piece rather than the whole
 * meeting. A MediaRecorder's later chunks cannot be decoded without its first
 * one, so each segment is its own recorder, started as the last one stops.
 *
 * Why a capture clock: the timer follows real capture (design, M7). A pause
 * the person chose and an interruption they did not both stop the clock; the
 * interruption is also reported as a gap with its wall-clock length, so the
 * record can say where capture was missing and why instead of a transcript
 * that silently skips.
 *
 * Nothing here talks to the server: segments and gaps go to callbacks, so
 * the upload path (with its retries) and the tests sit outside it.
 */

export type GapReason = "microphone_lost" | "backgrounded" | "offline" | "recorder_error";

export type Segment = {
    seq: number;
    blob: Blob;
    startMs: number;
    endMs: number;
    mimeType: string;
};

export type Gap = { atMs: number; durationMs: number | null; reason: GapReason };

export type RecorderState = "idle" | "recording" | "paused" | "interrupted" | "stopped";

type RecorderLike = {
    state: string;
    mimeType?: string;
    ondataavailable: ((event: { data: Blob }) => void) | null;
    onstop: (() => void) | null;
    onerror: ((event: unknown) => void) | null;
    start: () => void;
    stop: () => void;
};

export type RecorderOptions = {
    stream: MediaStream;
    segmentMs: number;
    onSegment: (segment: Segment) => void;
    onGap: (gap: Gap) => void;
    onState?: (state: RecorderState) => void;
    /** For tests. */
    now?: () => number;
    createRecorder?: (stream: MediaStream) => RecorderLike;
    isHidden?: () => boolean;
    setTimer?: (fn: () => void, ms: number) => unknown;
    clearTimer?: (handle: unknown) => void;
};

/** Milliseconds of real capture: counts only while running. */
export class CaptureClock {
    private total = 0;
    private since: number | null = null;

    constructor(private readonly now: () => number) {}

    run(): void {
        if (this.since === null) this.since = this.now();
    }

    hold(): void {
        if (this.since !== null) {
            this.total += Math.max(0, this.now() - this.since);
            this.since = null;
        }
    }

    get running(): boolean {
        return this.since !== null;
    }

    elapsed(): number {
        return this.total + (this.since !== null ? Math.max(0, this.now() - this.since) : 0);
    }
}

export class SegmentedRecorder {
    readonly clock: CaptureClock;
    private stream: MediaStream;
    private recorder: RecorderLike | null = null;
    private timer: unknown = null;
    private seq = -1;
    private stateValue: RecorderState = "idle";
    private interruptedAt: number | null = null;
    private interruptedReason: GapReason | null = null;
    private pending: Promise<void> = Promise.resolve();
    private readonly now: () => number;
    private readonly opts: RecorderOptions;

    constructor(opts: RecorderOptions) {
        this.opts = opts;
        this.stream = opts.stream;
        this.now = opts.now ?? (() => Date.now());
        this.clock = new CaptureClock(this.now);
        this.watchTracks();
    }

    get state(): RecorderState {
        return this.stateValue;
    }

    /** The highest segment number handed out, or -1 for none. */
    get lastSeq(): number {
        return this.seq;
    }

    private setState(next: RecorderState) {
        this.stateValue = next;
        this.opts.onState?.(next);
    }

    private watchTracks() {
        for (const track of this.stream.getAudioTracks()) {
            // A muted track gives silence or nothing: the browser took the
            // microphone away (another app, a backgrounded phone page).
            track.onmute = () => this.interrupt(this.hidden() ? "backgrounded" : "microphone_lost");
            track.onunmute = () => this.recover();
            // Ended is for good: the device was unplugged or permission
            // revoked. A new stream is needed (replaceStream).
            track.onended = () => this.interrupt("microphone_lost");
        }
    }

    private hidden(): boolean {
        if (this.opts.isHidden) return this.opts.isHidden();
        return typeof document !== "undefined" && document.visibilityState === "hidden";
    }

    private makeRecorder(): RecorderLike {
        if (this.opts.createRecorder) return this.opts.createRecorder(this.stream);
        return new MediaRecorder(this.stream) as unknown as RecorderLike;
    }

    /** Start one segment's recorder and its rotation timer. */
    private open() {
        const recorder = this.makeRecorder();
        const startMs = this.clock.elapsed();
        const chunks: Blob[] = [];
        let done: () => void = () => {};
        const finished = new Promise<void>((resolve) => {
            done = resolve;
        });
        this.pending = this.pending.then(() => finished);
        recorder.ondataavailable = (event) => {
            if (event.data && event.data.size > 0) chunks.push(event.data);
        };
        recorder.onstop = () => {
            const endMs = this.clock.elapsed();
            const mimeType = recorder.mimeType || "audio/webm";
            if (chunks.length > 0 && endMs > startMs) {
                this.seq += 1;
                this.opts.onSegment({
                    seq: this.seq,
                    blob: new Blob(chunks, { type: mimeType }),
                    startMs,
                    endMs,
                    mimeType,
                });
            }
            done();
        };
        recorder.onerror = () => this.interrupt("recorder_error");
        this.recorder = recorder;
        recorder.start();
        const set = this.opts.setTimer ?? ((fn: () => void, ms: number) => setTimeout(fn, ms));
        this.timer = set(() => this.rotate(), this.opts.segmentMs);
    }

    /** Close the current segment. The clock is read at its stop. */
    private close() {
        if (this.timer !== null) {
            const clear = this.opts.clearTimer ?? ((handle: unknown) => clearTimeout(handle as number));
            clear(this.timer);
            this.timer = null;
        }
        const recorder = this.recorder;
        this.recorder = null;
        if (recorder && recorder.state !== "inactive") recorder.stop();
    }

    private rotate() {
        if (this.stateValue !== "recording") return;
        this.close();
        this.open();
    }

    start() {
        if (this.stateValue !== "idle") return;
        this.clock.run();
        this.setState("recording");
        this.open();
    }

    pause() {
        if (this.stateValue !== "recording") return;
        this.close();
        this.clock.hold();
        this.setState("paused");
    }

    resume() {
        if (this.stateValue !== "paused") return;
        this.clock.run();
        this.setState("recording");
        this.open();
    }

    private interrupt(reason: GapReason) {
        if (this.stateValue !== "recording") return;
        this.close();
        this.clock.hold();
        this.interruptedAt = this.now();
        this.interruptedReason = reason;
        this.setState("interrupted");
    }

    /** The microphone is back on the same stream. */
    private recover() {
        if (this.stateValue !== "interrupted" || this.interruptedReason === "recorder_error") return;
        this.reportGap();
        this.clock.run();
        this.setState("recording");
        this.open();
    }

    private reportGap() {
        if (this.interruptedAt === null || this.interruptedReason === null) return;
        this.opts.onGap({
            atMs: this.clock.elapsed(),
            durationMs: Math.max(0, this.now() - this.interruptedAt),
            reason: this.interruptedReason,
        });
        this.interruptedAt = null;
        this.interruptedReason = null;
    }

    /** Carry on with a new microphone stream after the old one ended. */
    replaceStream(stream: MediaStream) {
        if (this.stateValue !== "interrupted") return;
        this.stream = stream;
        this.watchTracks();
        this.reportGap();
        this.clock.run();
        this.setState("recording");
        this.open();
    }

    /** Stop for good. Resolves once the last segment has been handed over. */
    async stop(): Promise<{ lastSeq: number; capturedMs: number }> {
        if (this.stateValue === "stopped") return { lastSeq: this.seq, capturedMs: this.clock.elapsed() };
        if (this.stateValue === "interrupted") this.reportGap();
        this.close();
        this.clock.hold();
        this.setState("stopped");
        await this.pending;
        return { lastSeq: this.seq, capturedMs: this.clock.elapsed() };
    }
}
