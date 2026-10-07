/**
 * Step, time and cost limits for one "work on my computer" task.
 *
 * Time is wall-clock spent working: the minutes a card waits for a person
 * are not counted, because a person reading slowly is not the agent running
 * away. Cost is the model calls only (the one thing that leaves the
 * machine), from the usage each response reports.
 */

import type { ModelUsage } from './types';

export interface Limits {
    maxSteps: number;
    maxSeconds: number;
    maxCostUsd: number;
}

export const DEFAULT_LIMITS: Limits = { maxSteps: 40, maxSeconds: 10 * 60, maxCostUsd: 2 };

/** Hard ceilings a person cannot raise past from settings. */
export const CEILINGS: Limits = { maxSteps: 200, maxSeconds: 60 * 60, maxCostUsd: 20 };

/** USD per million tokens, for the model the app is set to. */
export interface Rates {
    input: number;
    output: number;
    cacheRead: number;
    cacheWrite: number;
}

export function clampLimits(wanted: Partial<Limits>): Limits {
    const pick = (key: keyof Limits) => {
        const v = Number(wanted[key]);
        if (!Number.isFinite(v) || v <= 0) return DEFAULT_LIMITS[key];
        return Math.min(v, CEILINGS[key]);
    };
    return {
        maxSteps: Math.floor(pick('maxSteps')),
        maxSeconds: pick('maxSeconds'),
        maxCostUsd: pick('maxCostUsd'),
    };
}

export type LimitReason = 'steps' | 'time' | 'cost';

export class Budget {
    steps = 0;
    costUsd = 0;
    private workedMs = 0;
    private since: number | null;

    constructor(
        readonly limits: Limits,
        private readonly rates: Rates,
        private readonly now: () => number = Date.now,
    ) {
        this.since = now();
    }

    get seconds(): number {
        const running = this.since === null ? 0 : this.now() - this.since;
        return (this.workedMs + running) / 1000;
    }

    /** Stop the clock (waiting on a person). */
    pause(): void {
        if (this.since === null) return;
        this.workedMs += this.now() - this.since;
        this.since = null;
    }

    resume(): void {
        if (this.since === null) this.since = this.now();
    }

    addUsage(usage: ModelUsage | undefined): void {
        if (!usage) return;
        const m = 1_000_000;
        this.costUsd +=
            ((usage.input_tokens ?? 0) * this.rates.input +
                (usage.output_tokens ?? 0) * this.rates.output +
                (usage.cache_read_input_tokens ?? 0) * this.rates.cacheRead +
                (usage.cache_creation_input_tokens ?? 0) * this.rates.cacheWrite) /
            m;
    }

    /** Whether one more action may run. Counted before it runs. */
    takeStep(): LimitReason | null {
        const over = this.exceeded();
        if (over) return over;
        if (this.steps >= this.limits.maxSteps) return 'steps';
        this.steps += 1;
        return null;
    }

    exceeded(): LimitReason | null {
        if (this.steps > this.limits.maxSteps) return 'steps';
        if (this.seconds >= this.limits.maxSeconds) return 'time';
        if (this.costUsd >= this.limits.maxCostUsd) return 'cost';
        return null;
    }
}

export function describeLimit(reason: LimitReason, limits: Limits): string {
    if (reason === 'steps') return `Stopped at the step limit (${limits.maxSteps} actions).`;
    if (reason === 'time')
        return `Stopped at the time limit (${Math.round(limits.maxSeconds / 60)} minutes).`;
    return `Stopped at the cost limit ($${limits.maxCostUsd.toFixed(2)} of model calls).`;
}
