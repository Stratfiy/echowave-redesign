/**
 * The shapes the computer-use loop works with.
 *
 * The loop never touches the operating system itself. Everything it does to
 * the screen goes through a `ComputerDriver`, so tests run it against a fake
 * screen and the real app runs it against `native-driver.ts`. Everything it
 * asks a person goes through an `ApprovalClient`, so the approval card lives
 * in the web app (services/workflow/actions.py) and not in this process.
 */

export type Point = [number, number];
export type Region = [number, number, number, number];

export type MouseButton = 'left' | 'right' | 'middle';

/** The app whose window is in front. `name` is what the person sees. */
export interface FrontApp {
    name: string;
    /** macOS bundle id or Windows executable name, when the OS tells us. */
    id?: string;
    windowTitle?: string;
}

/**
 * The element that has keyboard focus. `secure` is the whole point: true is
 * a password field, false is known not to be one, and null is "the OS would
 * not say" -- which the loop treats as a password field, because guessing
 * wrong in the other direction types somebody's password into a model.
 */
export interface FocusedElement {
    secure: boolean | null;
    role?: string;
    label?: string;
}

/** What is under a point, read from the accessibility tree when possible. */
export interface ElementInfo {
    role?: string;
    label?: string;
}

/** A capture, already scaled to fit the model's image limits. */
export interface Screenshot {
    /** base64, no data: prefix */
    data: string;
    mediaType: 'image/png' | 'image/jpeg';
    width: number;
    height: number;
}

export interface ComputerDriver {
    /** The real screen in the same pixels the input calls use. */
    screenSize(): Promise<{ width: number; height: number }>;
    /** The whole screen, scaled so its long edge is at most `maxLongEdge`. */
    screenshot(maxLongEdge: number): Promise<Screenshot>;
    /** A region in real-screen pixels, scaled to fit `maxLongEdge`. */
    zoom(region: Region, maxLongEdge: number): Promise<Screenshot>;
    frontApp(): Promise<FrontApp | null>;
    focusedElement(): Promise<FocusedElement>;
    elementAt(point: Point): Promise<ElementInfo | null>;
    activateApp(name: string): Promise<void>;

    click(
        button: MouseButton,
        point: Point | undefined,
        count: 1 | 2 | 3,
        modifiers: string[],
    ): Promise<void>;
    drag(from: Point, to: Point, modifiers: string[]): Promise<void>;
    move(point: Point): Promise<void>;
    mouseDown(): Promise<void>;
    mouseUp(): Promise<void>;
    cursor(): Promise<Point>;
    scroll(
        direction: 'up' | 'down' | 'left' | 'right',
        amount: number,
        point: Point | undefined,
        modifiers: string[],
    ): Promise<void>;
    typeText(text: string): Promise<void>;
    pressKeys(combo: string, repeat: number): Promise<void>;
    holdKeys(combo: string, seconds: number): Promise<void>;
    wait(seconds: number): Promise<void>;
}

/** The four things that never happen without a person saying yes. */
export type ConsequentialKind = 'send' | 'pay' | 'delete' | 'submit';

export interface ApprovalRequest {
    kind: ConsequentialKind;
    app: string;
    /** One plain sentence: "Send the reply to Asha in Mail". */
    summary: string;
    /** The exact detail: who, what, how much. Shown as written. */
    detail: string;
    /** The exact action that will run, in real-screen pixels. */
    step: { name: string; input: Record<string, unknown> };
    /** sha256 over app + step; the server will release only this. */
    fingerprint: string;
}

export type ApprovalOutcome = 'released' | 'declined' | 'cancelled' | 'expired' | 'unavailable';

export interface ApprovalClient {
    propose(request: ApprovalRequest): Promise<{ id: number }>;
    /** Resolves once the card has left `proposed`/`armed`. */
    waitForDecision(id: number, signal: AbortSignal): Promise<ApprovalOutcome>;
    /** Compare-and-swap on the server: true exactly once per card. */
    claim(id: number, fingerprint: string): Promise<boolean>;
    report(id: number, ok: boolean, note: string): Promise<void>;
    /** Stop pressed while the card was waiting: take it off the thread. */
    cancel(id: number): Promise<void>;
}

/** The tool_use block shape we read from a response (SDK-agnostic). */
export interface ToolUseBlock {
    type: 'tool_use';
    id: string;
    name: string;
    input: Record<string, unknown>;
    toolset_name?: string;
}

export interface ModelUsage {
    input_tokens?: number;
    output_tokens?: number;
    cache_read_input_tokens?: number | null;
    cache_creation_input_tokens?: number | null;
}

export interface ModelResponse {
    content: Array<{ type: string; [key: string]: unknown }>;
    stop_reason: string | null;
    usage?: ModelUsage;
}

export interface ModelRequest {
    system: string;
    tools: Array<Record<string, unknown>>;
    messages: Array<{ role: 'user' | 'assistant'; content: unknown }>;
}

/** The one call that leaves the machine. */
export interface ModelClient {
    create(request: ModelRequest, signal: AbortSignal): Promise<ModelResponse>;
}

export type AgentEvent =
    | { type: 'started'; task: string }
    | { type: 'step'; index: number; app: string; action: string; detail: string }
    | { type: 'blocked'; reason: string }
    | { type: 'awaiting_approval'; id: number; summary: string }
    | { type: 'approval_settled'; id: number; outcome: ApprovalOutcome | 'claimed' | 'not_claimed' }
    | { type: 'progress'; steps: number; seconds: number; costUsd: number }
    | { type: 'finished'; receiptId: string; reason: string };
