/**
 * Playing a live call: the caller and the agent, mixed in the browser.
 *
 * Each binary message from the listen socket is one packet:
 * `side (1 byte, "c", "a" or "s") | sample rate (u32 LE) | channels (u8) | PCM s16le`
 * (api/services/live_supervision/channels.py). "s" is a supervisor who has
 * joined the call (api/services/live_takeover); the one speaking leaves
 * themselves out (`skipSupervisor`), everybody else hears them. Each side is scheduled on its
 * own clock, at its own rate -- Web Audio resamples -- and both play into the
 * same output, which is the mix. Receive-only: no microphone is asked for.
 *
 * The agent's audio arrives faster than real time (it is taken before the
 * call's output paces it), so it is queued; when the agent is interrupted on
 * the call, `interrupt()` drops what is queued so the listener does not hear
 * words the caller never did.
 */

export type Side = 'caller' | 'agent' | 'supervisor';
export type Packet = { side: Side; sampleRate: number; channels: number; samples: Float32Array };

const SIDES: Record<string, Side> = { c: 'caller', a: 'agent', s: 'supervisor' };

const HEADER = 6;
/** Behind by more than this, a side skips ahead rather than fall further back. */
const MAX_LAG_SECONDS = 1.5;

export function parsePacket(buffer: ArrayBuffer): Packet | null {
    if (buffer.byteLength < HEADER) return null;
    const view = new DataView(buffer);
    const side = SIDES[String.fromCharCode(view.getUint8(0))] ?? 'agent';
    const sampleRate = view.getUint32(1, true);
    const channels = view.getUint8(5) || 1;
    const count = Math.floor((buffer.byteLength - HEADER) / 2);
    const samples = new Float32Array(count);
    for (let i = 0; i < count; i++) samples[i] = view.getInt16(HEADER + i * 2, true) / 32768;
    if (!sampleRate) return null;
    return { side, sampleRate, channels, samples };
}

export class LivePlayer {
    private context: AudioContext;
    private next: Record<Side, number> = { caller: 0, agent: 0, supervisor: 0 };
    /** Set while this listener is the supervisor speaking on the call. */
    skipSupervisor = false;
    private agentSources: AudioBufferSourceNode[] = [];

    constructor(context?: AudioContext) {
        this.context = context ?? new AudioContext();
    }

    async resume(): Promise<void> {
        if (this.context.state === 'suspended') await this.context.resume();
    }

    play(packet: Packet): void {
        if (packet.side === 'supervisor' && this.skipSupervisor) return;
        const frames = Math.floor(packet.samples.length / packet.channels);
        if (!frames) return;
        const buffer = this.context.createBuffer(packet.channels, frames, packet.sampleRate);
        for (let ch = 0; ch < packet.channels; ch++) {
            const data = buffer.getChannelData(ch);
            for (let i = 0; i < frames; i++) data[i] = packet.samples[i * packet.channels + ch];
        }
        const source = this.context.createBufferSource();
        source.buffer = buffer;
        source.connect(this.context.destination);
        const now = this.context.currentTime;
        let start = this.next[packet.side];
        if (start < now || start - now > MAX_LAG_SECONDS) start = now + 0.05;
        source.start(start);
        this.next[packet.side] = start + buffer.duration;
        if (packet.side === 'agent') {
            this.agentSources.push(source);
            source.onended = () => {
                this.agentSources = this.agentSources.filter((s) => s !== source);
            };
        }
    }

    interrupt(): void {
        for (const source of this.agentSources) {
            try {
                source.stop();
            } catch {
                // Already finished.
            }
        }
        this.agentSources = [];
        this.next.agent = 0;
    }

    async close(): Promise<void> {
        this.interrupt();
        await this.context.close().catch(() => undefined);
    }
}
