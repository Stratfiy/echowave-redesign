import { describe, expect, it } from 'vitest';

import { DEFAULT_LIMITS } from '../src/computer-use/limits';
import type { ModelClient } from '../src/computer-use/types';
import { type ComputerDeps, ComputerSessions } from '../src/main/computer';
import { FakeApprovals, FakeDriver, RATES, ScriptedModel, done, reply, use } from './fakes';

function sessions(
    overrides: Partial<ComputerDeps> = {},
    model: ModelClient = new ScriptedModel([done()]),
) {
    const log: string[] = [];
    const driver = new FakeDriver();
    const approvals = new FakeApprovals();
    const s = new ComputerSessions({
        driver: () => driver,
        model: () => model,
        approvals: () => approvals,
        available: async () => true,
        signedIn: () => true,
        config: () => ({
            rules: { mail: { allowed: true } },
            allowedApps: ['Mail'],
            limits: DEFAULT_LIMITS,
            rates: RATES,
        }),
        bar: {
            show: () => log.push('bar:show'),
            update: (e) => log.push(`bar:${e.type}`),
            hide: () => log.push('bar:hide'),
        },
        saveReceipt: () => log.push('saved'),
        postReceipt: async (text) => {
            log.push(`posted:${text.split('\n')[0]}`);
            return true;
        },
        notify: (title) => log.push(`notify:${title}`),
        newId: () => 'session-1',
        ...overrides,
    });
    return { s, log, driver, approvals };
}

describe('starting', () => {
    it('refuses with a reason the person can act on', async () => {
        expect(await sessions({ signedIn: () => false }).s.start('x')).toEqual({
            started: false,
            reason: 'Sign in to Decibyl in this app first.',
        });
        expect(await sessions({ available: async () => false }).s.start('x')).toMatchObject({
            started: false,
            reason: expect.stringMatching(/not switched on/),
        });
        expect(
            await sessions({
                config: () => ({
                    rules: {},
                    allowedApps: [],
                    limits: DEFAULT_LIMITS,
                    rates: RATES,
                }),
            }).s.start('x'),
        ).toMatchObject({ started: false, reason: expect.stringMatching(/Pick at least one app/) });
        expect(await sessions({ model: () => null }).s.start('x')).toMatchObject({
            started: false,
            reason: expect.stringMatching(/model key/),
        });
        expect(await sessions().s.start('   ')).toMatchObject({ started: false });
    });

    it('shows the bar while working, then saves and posts the receipt', async () => {
        const { s, log } = sessions();
        expect(await s.start('Reply to Asha')).toEqual({ started: true, sessionId: 'session-1' });
        expect(s.active).toBe(true);
        expect(await s.start('another')).toMatchObject({
            started: false,
            reason: expect.stringMatching(/already working/),
        });
        const receipt = await s.settled();
        expect(receipt?.endReason).toBe('finished');
        expect(s.active).toBe(false);
        expect(log[0]).toBe('bar:show');
        expect(log).toContain('bar:hide');
        expect(log).toContain('saved');
        expect(log.find((l) => l.startsWith('posted:'))).toBe(
            'posted:Worked on your computer: Reply to Asha',
        );
    });
});

describe('Stop', () => {
    it('ends a running task: no action after it, the bar goes, the receipt says so', async () => {
        let session: ComputerSessions | null = null;
        const model = new ScriptedModel([
            () => {
                session?.stop();
                return reply([use('left_click', { coordinate: [1, 1] })]);
            },
            done(),
        ]);
        const { s, driver, log } = sessions({}, model);
        session = s;
        await s.start('Clean up the desktop');
        const receipt = await s.settled();
        expect(driver.calls).toEqual([]);
        expect(receipt?.endReason).toBe('stopped');
        expect(receipt?.endNote).toBe('Stopped by you.');
        expect(log).toContain('bar:hide');
        expect(s.stop()).toBe(false);
    });
});
