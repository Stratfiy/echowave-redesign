/**
 * The receipt: what Decibyl did on this computer, in order.
 *
 * Text only. No screenshot is ever written into it -- screenshots live in
 * memory for the length of one model call and nowhere else. Typed text is
 * kept (it is what the person will want to check) but cut short.
 */

import { randomUUID } from 'node:crypto';

export type StepOutcome = 'done' | 'refused' | 'failed' | 'not_run' | 'declined';

export interface ReceiptStep {
    index: number;
    at: string;
    app: string;
    action: string;
    detail: string;
    outcome: StepOutcome;
    note?: string;
    /** The approval card this step ran under, when it needed one. */
    approvalId?: number;
}

export interface Receipt {
    id: string;
    task: string;
    startedAt: string;
    endedAt?: string;
    /** finished | stopped | limit | refused | error */
    endReason?: string;
    endNote?: string;
    summary?: string;
    steps: ReceiptStep[];
    totals: { steps: number; seconds: number; costUsd: number; approvals: number };
}

const MAX_DETAIL = 160;

export function shorten(text: string, max = MAX_DETAIL): string {
    const flat = text.replace(/\s+/g, ' ').trim();
    return flat.length > max ? `${flat.slice(0, max - 1)}…` : flat;
}

/** One line for an action, for the bar and the receipt. */
export function describeStep(name: string, input: Record<string, unknown>): string {
    const at = Array.isArray(input.coordinate) ? ` at ${input.coordinate.join(',')}` : '';
    switch (name) {
        case 'type':
            return `Typed "${shorten(String(input.text ?? ''), 80)}"`;
        case 'key':
            return `Pressed ${String(input.text ?? '')}${Number(input.repeat) > 1 ? ` ×${input.repeat}` : ''}`;
        case 'scroll':
            return `Scrolled ${String(input.scroll_direction ?? '')} ${String(input.scroll_amount ?? '')}${at}`;
        case 'left_click_drag':
            return `Dragged to ${Array.isArray(input.coordinate) ? input.coordinate.join(',') : ''}`;
        case 'screenshot':
            return 'Looked at the screen';
        case 'zoom':
            return 'Looked closer at part of the screen';
        default:
            return `${name.replace(/_/g, ' ')}${at}`.replace(/^./, (c) => c.toUpperCase());
    }
}

export class ReceiptWriter {
    readonly receipt: Receipt;

    constructor(task: string, now: () => Date = () => new Date()) {
        this.now = now;
        this.receipt = {
            id: randomUUID(),
            task: shorten(task, 500),
            startedAt: now().toISOString(),
            steps: [],
            totals: { steps: 0, seconds: 0, costUsd: 0, approvals: 0 },
        };
    }

    private readonly now: () => Date;

    add(step: Omit<ReceiptStep, 'index' | 'at'>): ReceiptStep {
        const entry: ReceiptStep = {
            ...step,
            detail: shorten(step.detail),
            index: this.receipt.steps.length + 1,
            at: this.now().toISOString(),
        };
        this.receipt.steps.push(entry);
        if (step.approvalId !== undefined && step.outcome === 'done')
            this.receipt.totals.approvals += 1;
        return entry;
    }

    finish(
        reason: string,
        note: string,
        totals: { steps: number; seconds: number; costUsd: number },
        summary?: string,
    ): Receipt {
        this.receipt.endedAt = this.now().toISOString();
        this.receipt.endReason = reason;
        this.receipt.endNote = note;
        if (summary) this.receipt.summary = shorten(summary, 1000);
        this.receipt.totals = { ...this.receipt.totals, ...totals };
        return this.receipt;
    }
}

/** The receipt as a few sentences, for the thread and the notification. */
export function receiptText(receipt: Receipt): string {
    const done = receipt.steps.filter((s) => s.outcome === 'done').length;
    const refused = receipt.steps.filter(
        (s) => s.outcome === 'refused' || s.outcome === 'declined',
    ).length;
    const lines = [
        `Worked on your computer: ${receipt.task}`,
        `${done} action${done === 1 ? '' : 's'} done${refused ? `, ${refused} not done` : ''}, ${receipt.totals.approvals} approved by you, ${Math.round(receipt.totals.seconds)}s, about $${receipt.totals.costUsd.toFixed(2)} of model calls.`,
    ];
    if (receipt.endNote) lines.push(receipt.endNote);
    if (receipt.summary) lines.push(receipt.summary);
    return lines.join('\n');
}
