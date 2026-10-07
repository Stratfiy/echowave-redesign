/** A fake screen, a scripted model and a fake approval card, for tests. */

import type {
    ApprovalClient,
    ApprovalOutcome,
    ApprovalRequest,
    ComputerDriver,
    ElementInfo,
    FocusedElement,
    FrontApp,
    ModelClient,
    ModelRequest,
    ModelResponse,
    Point,
} from '../src/computer-use/types';

export class FakeDriver implements ComputerDriver {
    calls: Array<{ name: string; args: unknown[] }> = [];
    screenshots = 0;
    front: FrontApp | null = { name: 'Mail' };
    focused: FocusedElement = { secure: false, role: 'AXTextField' };
    /** Label under any point, or a function of the point. */
    label: string | ((p: Point) => string | undefined) | undefined = undefined;
    size = { width: 3840, height: 2160 };
    onAction?: (name: string) => void;

    private record(name: string, ...args: unknown[]) {
        this.calls.push({ name, args });
        this.onAction?.(name);
    }

    actions(): string[] {
        return this.calls.map((c) => c.name);
    }

    async screenSize() {
        return this.size;
    }
    async screenshot(maxLongEdge: number) {
        this.screenshots += 1;
        const s = Math.min(1, maxLongEdge / Math.max(this.size.width, this.size.height));
        return {
            data: 'SCREENSHOT_BYTES',
            mediaType: 'image/png' as const,
            width: Math.round(this.size.width * s),
            height: Math.round(this.size.height * s),
        };
    }
    async zoom() {
        this.screenshots += 1;
        return { data: 'ZOOM_BYTES', mediaType: 'image/png' as const, width: 100, height: 100 };
    }
    async frontApp() {
        return this.front;
    }
    async focusedElement() {
        return this.focused;
    }
    async elementAt(p: Point): Promise<ElementInfo | null> {
        const label = typeof this.label === 'function' ? this.label(p) : this.label;
        return label ? { role: 'button', label } : null;
    }
    async activateApp(name: string) {
        this.record('activateApp', name);
        this.front = { name };
    }
    async click(button: string, point: Point | undefined, count: number, modifiers: string[]) {
        this.record('click', button, point, count, modifiers);
    }
    async drag(from: Point, to: Point) {
        this.record('drag', from, to);
    }
    async move(p: Point) {
        this.record('move', p);
    }
    async mouseDown() {
        this.record('mouseDown');
    }
    async mouseUp() {
        this.record('mouseUp');
    }
    async cursor(): Promise<Point> {
        return [100, 100];
    }
    async scroll(direction: string, amount: number, point: Point | undefined) {
        this.record('scroll', direction, amount, point);
    }
    async typeText(text: string) {
        this.record('type', text);
    }
    async pressKeys(combo: string, repeat: number) {
        this.record('key', combo, repeat);
    }
    async holdKeys(combo: string, seconds: number) {
        this.record('hold', combo, seconds);
    }
    async wait() {
        this.record('wait');
    }
}

let n = 0;
export function use(name: string, input: Record<string, unknown> = {}, computer = true) {
    n += 1;
    return {
        type: 'tool_use',
        id: `toolu_${n}`,
        name,
        input,
        ...(computer ? { toolset_name: 'computer' } : {}),
    };
}

export function reply(
    content: Array<Record<string, unknown>>,
    stop = 'tool_use',
    usage = { input_tokens: 1000, output_tokens: 100 },
): ModelResponse {
    return { content: content as ModelResponse['content'], stop_reason: stop, usage };
}

export function done(text = 'All done.'): ModelResponse {
    return reply([{ type: 'text', text }], 'end_turn');
}

/** Plays back responses in order; records every request it was sent. */
export class ScriptedModel implements ModelClient {
    requests: ModelRequest[] = [];
    constructor(
        private readonly script: Array<ModelResponse | ((req: ModelRequest) => ModelResponse)>,
    ) {}
    async create(request: ModelRequest, signal: AbortSignal): Promise<ModelResponse> {
        if (signal.aborted) throw Object.assign(new Error('aborted'), { name: 'AbortError' });
        this.requests.push(structuredClone(request));
        const next = this.script.shift();
        if (!next) return done();
        return typeof next === 'function' ? next(request) : next;
    }
    /** The tool_result blocks the loop sent back in the last request. */
    lastResults(): Array<Record<string, unknown>> {
        const last = this.requests[this.requests.length - 1];
        const msg = last.messages[last.messages.length - 1];
        return msg.content as Array<Record<string, unknown>>;
    }
}

export class FakeApprovals implements ApprovalClient {
    proposed: ApprovalRequest[] = [];
    claims: Array<{ id: number; fingerprint: string }> = [];
    reports: Array<{ id: number; ok: boolean; note: string }> = [];
    cancelled: number[] = [];
    outcome: ApprovalOutcome = 'released';
    claimResult = true;
    /** Called while waiting; a test can press Stop here. */
    whileWaiting?: () => void;
    private used = new Set<number>();

    async propose(request: ApprovalRequest) {
        this.proposed.push(request);
        return { id: 100 + this.proposed.length };
    }
    async waitForDecision(_id: number, signal: AbortSignal) {
        this.whileWaiting?.();
        if (signal.aborted) return 'cancelled' as const;
        return this.outcome;
    }
    async claim(id: number, fingerprint: string) {
        this.claims.push({ id, fingerprint });
        if (!this.claimResult) return false;
        // Run once: a second claim of the same card is refused, as on the server.
        const expected = this.proposed[id - 101]?.fingerprint;
        if (this.used.has(id) || fingerprint !== expected) return false;
        this.used.add(id);
        return true;
    }
    async report(id: number, ok: boolean, note: string) {
        this.reports.push({ id, ok, note });
    }
    async cancel(id: number) {
        this.cancelled.push(id);
    }
}

export const RATES = { input: 4, output: 20, cacheRead: 0.2, cacheWrite: 5 };
