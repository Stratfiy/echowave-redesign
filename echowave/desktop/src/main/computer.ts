/**
 * One "work on my computer" task at a time, and everything around it: the
 * checks before it starts, the Stop that ends it, the bar that shows it, and
 * the receipt it leaves.
 *
 * It refuses to start -- with a reason the person can act on -- when the
 * switch is off for their organisation, they are not signed in, no app is on
 * their list, or no model key is set. It never starts on its own: only a
 * person's request from the web app or the quick ask window starts it.
 */

import { runComputerTask } from '../computer-use/agent';
import type { Limits, Rates } from '../computer-use/limits';
import type { AppRules } from '../computer-use/policy';
import { type Receipt, receiptText } from '../computer-use/receipt';
import type {
    AgentEvent,
    ApprovalClient,
    ComputerDriver,
    ModelClient,
} from '../computer-use/types';

export interface ComputerDeps {
    driver: () => ComputerDriver;
    model: () => ModelClient | null;
    approvals: (sessionId: string) => ApprovalClient;
    available: () => Promise<boolean>;
    signedIn: () => boolean;
    config: () => { rules: AppRules; allowedApps: string[]; limits: Limits; rates: Rates };
    /** The always-visible bar. */
    bar: { show(task: string): void; update(event: AgentEvent): void; hide(): void };
    onEvent?: (event: AgentEvent) => void;
    saveReceipt: (receipt: Receipt) => void;
    postReceipt: (text: string) => Promise<boolean>;
    notify: (title: string, body: string) => void;
    newId: () => string;
}

export type StartResult = { started: true; sessionId: string } | { started: false; reason: string };

export class ComputerSessions {
    private controller: AbortController | null = null;
    private running: Promise<Receipt> | null = null;
    private task = '';
    last: Receipt | null = null;

    constructor(private readonly deps: ComputerDeps) {}

    get active(): boolean {
        return this.controller !== null;
    }

    status() {
        return {
            active: this.active,
            task: this.active ? this.task : null,
            lastReceipt: this.last,
        };
    }

    async start(task: string): Promise<StartResult> {
        const clean = task.trim();
        if (!clean) return { started: false, reason: 'Say what to do.' };
        if (this.active)
            return {
                started: false,
                reason: 'Decibyl is already working on your computer. Stop it first.',
            };
        if (!this.deps.signedIn())
            return { started: false, reason: 'Sign in to Decibyl in this app first.' };
        if (!(await this.deps.available())) {
            return {
                started: false,
                reason: 'Working on your computer is not switched on for your workspace yet.',
            };
        }
        const config = this.deps.config();
        if (config.allowedApps.length === 0) {
            return {
                started: false,
                reason: 'Pick at least one app Decibyl may use, in Decibyl settings on this computer.',
            };
        }
        const model = this.deps.model();
        if (!model)
            return {
                started: false,
                reason: 'Add a model key in Decibyl settings on this computer.',
            };

        const sessionId = this.deps.newId();
        const controller = new AbortController();
        this.controller = controller;
        this.task = clean;
        this.deps.bar.show(clean);
        const emit = (event: AgentEvent) => {
            this.deps.bar.update(event);
            this.deps.onEvent?.(event);
        };
        this.running = runComputerTask({
            task: clean,
            driver: this.deps.driver(),
            model,
            approvals: this.deps.approvals(sessionId),
            rules: config.rules,
            allowedApps: config.allowedApps,
            limits: config.limits,
            rates: config.rates,
            signal: controller.signal,
            onEvent: emit,
        }).then(
            (receipt) => this.finish(receipt),
            (err) => {
                // runComputerTask records its own failures; this is a bug
                // before the loop started (a driver that would not load).
                this.controller = null;
                this.deps.bar.hide();
                this.deps.notify(
                    'Decibyl could not start',
                    (err as Error)?.message ?? 'Something went wrong.',
                );
                throw err;
            },
        );
        this.running.catch(() => undefined);
        return { started: true, sessionId };
    }

    /** Stop now: the model call is aborted, no further action runs, a
     *  waiting approval card is taken off the thread. */
    stop(): boolean {
        if (!this.controller) return false;
        this.controller.abort();
        return true;
    }

    /** For tests and for quitting: wait for the loop to wind down. */
    async settled(): Promise<Receipt | null> {
        if (!this.running) return this.last;
        try {
            return await this.running;
        } catch {
            return null;
        }
    }

    private async finish(receipt: Receipt): Promise<Receipt> {
        this.controller = null;
        this.last = receipt;
        this.deps.bar.hide();
        this.deps.saveReceipt(receipt);
        const text = receiptText(receipt);
        await this.deps.postReceipt(text).catch(() => false);
        this.deps.notify(
            'Decibyl finished on your computer',
            receipt.endNote || receipt.summary || 'Done.',
        );
        return receipt;
    }
}
