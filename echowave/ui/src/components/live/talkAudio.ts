/**
 * The supervisor's microphone, as the talking socket wants it.
 *
 * One binary message per slice, in the listen socket's packet format with
 * side "s": `"s" | sample rate (u32 LE) | channels (u8) | PCM s16le`, mono at
 * 16 kHz (api/services/live_takeover/channels.py). The browser records at
 * whatever rate its audio device runs; slices are brought down to 16 kHz
 * here, so the call's worker never receives more than it needs.
 *
 * The microphone is opened only from an explicit action (joining the call),
 * with the browser's echo cancellation and noise suppression asked for, and
 * closed the moment the supervisor leaves the call.
 */

export const MIC_RATE = 16000;
const HEADER = 6;
/** About 85 ms at 48 kHz: small enough to feel live, large enough to be cheap. */
const BUFFER_SIZE = 4096;

/** Linear interpolation down to `outRate`; returned as-is when no lower. */
export function downsample(input: Float32Array, inRate: number, outRate: number): Float32Array {
    if (outRate >= inRate) return input;
    const ratio = inRate / outRate;
    const length = Math.floor(input.length / ratio);
    const out = new Float32Array(length);
    for (let i = 0; i < length; i++) {
        const at = i * ratio;
        const left = Math.floor(at);
        const right = Math.min(left + 1, input.length - 1);
        const frac = at - left;
        out[i] = input[left] * (1 - frac) + input[right] * frac;
    }
    return out;
}

export function encodeMic(samples: Float32Array, sampleRate: number = MIC_RATE): ArrayBuffer {
    const buffer = new ArrayBuffer(HEADER + samples.length * 2);
    const view = new DataView(buffer);
    view.setUint8(0, 's'.charCodeAt(0));
    view.setUint32(1, sampleRate, true);
    view.setUint8(5, 1);
    for (let i = 0; i < samples.length; i++) {
        const s = Math.max(-1, Math.min(1, samples[i]));
        view.setInt16(HEADER + i * 2, s < 0 ? s * 0x8000 : s * 0x7fff, true);
    }
    return buffer;
}

/** 0..1, for the level meter beside "You're live". */
export function levelOf(samples: Float32Array): number {
    if (!samples.length) return 0;
    let sum = 0;
    for (let i = 0; i < samples.length; i++) sum += samples[i] * samples[i];
    return Math.min(1, Math.sqrt(sum / samples.length) * 4);
}

export class Microphone {
    onSamples: ((samples: Float32Array, sampleRate: number) => void) | null = null;

    private constructor(
        private stream: MediaStream,
        private context: AudioContext,
        private node: ScriptProcessorNode,
        private source: MediaStreamAudioSourceNode,
    ) {}

    /** Asks the browser for the microphone. Throws when it is refused. */
    static async open(): Promise<Microphone> {
        const stream = await navigator.mediaDevices.getUserMedia({
            audio: { echoCancellation: true, noiseSuppression: true, autoGainControl: true, channelCount: 1 },
        });
        const context = new AudioContext();
        const source = context.createMediaStreamSource(stream);
        // ScriptProcessorNode is old, but it needs no separate worklet file
        // and every browser that can take a call still has it.
        const node = context.createScriptProcessor(BUFFER_SIZE, 1, 1);
        const mic = new Microphone(stream, context, node, source);
        node.onaudioprocess = (event) => {
            const input = event.inputBuffer.getChannelData(0);
            mic.onSamples?.(new Float32Array(input), context.sampleRate);
        };
        source.connect(node);
        // Connected so the node runs; its output is silence.
        node.connect(context.destination);
        if (context.state === 'suspended') await context.resume();
        return mic;
    }

    close(): void {
        this.onSamples = null;
        try {
            this.source.disconnect();
            this.node.disconnect();
        } catch {
            // Already disconnected.
        }
        for (const track of this.stream.getTracks()) track.stop();
        void this.context.close().catch(() => undefined);
    }
}
