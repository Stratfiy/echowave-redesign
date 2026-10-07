import { describe, expect, it } from 'vitest';

import { MAX_LONG_EDGE, fingerprint, runComputerTask } from '../src/computer-use/agent';
import { DEFAULT_LIMITS } from '../src/computer-use/limits';
import { FakeApprovals, FakeDriver, RATES, ScriptedModel, done, reply, use } from './fakes';

const RULES = { mail: { allowed: true }, notes: { allowed: true } };
const APPS = ['Mail', 'Notes'];

function setup(
    script: ConstructorParameters<typeof ScriptedModel>[0],
    overrides: Partial<Parameters<typeof runComputerTask>[0]> = {},
) {
    const driver = new FakeDriver();
    const model = new ScriptedModel(script);
    const approvals = new FakeApprovals();
    const controller = new AbortController();
    const events: string[] = [];
    const run = () =>
        runComputerTask({
            task: 'Reply to Asha',
            driver,
            model,
            approvals,
            rules: RULES,
            allowedApps: APPS,
            limits: DEFAULT_LIMITS,
            rates: RATES,
            signal: controller.signal,
            onEvent: (e) => events.push(e.type),
            ...overrides,
        });
    return { driver, model, approvals, controller, events, run };
}

describe('the request', () => {
    it('declares the computer toolset with no name or display size, and the approval tool', async () => {
        const { model, run } = setup([done()]);
        await run();
        const tools = model.requests[0].tools;
        expect(tools[0]).toEqual({ type: 'computer_toolset_20260801' });
        expect(tools.map((t) => t.name)).toContain('request_approval');
        expect(model.requests[0].system).toContain('Mail, Notes');
    });
});

describe('ordinary actions', () => {
    it('runs allowed actions, scales coordinates back to the real screen, and echoes toolset_name', async () => {
        const { driver, model, run } = setup([
            reply([use('screenshot'), use('left_click', { coordinate: [960, 540] })]),
            done(),
        ]);
        const receipt = await run();
        // 3840 wide screen, 1920 to the model: a factor of two.
        expect(driver.calls).toEqual([{ name: 'click', args: ['left', [1920, 1080], 1, []] }]);
        const results = model.lastResults();
        expect(results).toHaveLength(2);
        for (const r of results) expect(r.toolset_name).toBe('computer');
        expect((results[0].content as Array<{ type: string }>)[0].type).toBe('image');
        expect(receipt.endReason).toBe('finished');
        expect(receipt.summary).toBe('All done.');
    });

    it('sends screenshots no larger than the long-edge cap', async () => {
        const { driver, run } = setup([reply([use('screenshot')]), done()]);
        await run();
        const shot = await driver.screenshot(MAX_LONG_EDGE);
        expect(Math.max(shot.width, shot.height)).toBeLessThanOrEqual(MAX_LONG_EDGE);
    });

    it('rejects a point off the screen without acting', async () => {
        const { driver, model, run } = setup([
            reply([use('left_click', { coordinate: [5000, 10] })]),
            done(),
        ]);
        await run();
        expect(driver.calls).toEqual([]);
        expect(model.lastResults()[0].is_error).toBe(true);
    });
});

describe('the apps a person allowed', () => {
    it('refuses to touch an app that is not on the list, and says which', async () => {
        const { driver, model, events, run } = setup([
            reply([use('left_click', { coordinate: [10, 10] })]),
            done(),
        ]);
        driver.front = { name: 'Slack' };
        const receipt = await run();
        expect(driver.calls).toEqual([]);
        expect(JSON.stringify(model.lastResults()[0].content)).toContain('Slack');
        expect(events).toContain('blocked');
        expect(receipt.steps[0].outcome).toBe('refused');
    });

    it('does not even take a screenshot of an app that is not on the list', async () => {
        const { driver, run } = setup([reply([use('screenshot')]), done()]);
        driver.front = { name: 'WhatsApp' };
        await run();
        expect(driver.screenshots).toBe(0);
    });

    it('open_app only opens listed apps', async () => {
        const { driver, model, run } = setup([
            reply([use('open_app', { name: 'Terminal' }, false)]),
            reply([use('open_app', { name: 'notes' }, false)]),
            done(),
        ]);
        await run();
        expect(driver.calls).toEqual([{ name: 'activateApp', args: ['Notes'] }]);
        expect(model.requests[1].messages.at(-1)).toMatchObject({ content: [{ is_error: true }] });
    });
});

describe('never touch passwords or security windows', () => {
    it('refuses to type into a password field', async () => {
        const { driver, model, run } = setup([reply([use('type', { text: 'hunter2' })]), done()]);
        driver.focused = { secure: true, role: 'AXSecureTextField' };
        const receipt = await run();
        expect(driver.calls).toEqual([]);
        expect(JSON.stringify(model.lastResults()[0].content)).toMatch(
            /never types into password fields/,
        );
        // The refused text is not in the receipt.
        expect(JSON.stringify(receipt)).not.toContain('hunter2');
    });

    it('refuses to type when the OS will not say whether the field is a password field', async () => {
        const { driver, run } = setup([reply([use('type', { text: 'hello' })]), done()]);
        driver.focused = { secure: null };
        await run();
        expect(driver.calls).toEqual([]);
    });

    it('refuses key presses into a password field but lets Tab move away', async () => {
        const { driver, run } = setup([
            reply([use('key', { text: 'a' }), use('key', { text: 'ctrl+v' })]),
            done(),
        ]);
        driver.focused = { secure: true };
        await run();
        expect(driver.calls).toEqual([]);
        const second = setup([reply([use('key', { text: 'Tab' })]), done()]);
        second.driver.focused = { secure: true };
        await second.run();
        expect(second.driver.calls).toEqual([{ name: 'key', args: ['Tab', 1] }]);
    });

    it('never looks at or acts on a system security dialog, even if the person listed it', async () => {
        const { driver, model, run } = setup(
            [reply([use('screenshot'), use('left_click', { coordinate: [1, 1] })]), done()],
            {
                rules: { ...RULES, securityagent: { allowed: true } },
            },
        );
        driver.front = { name: 'SecurityAgent' };
        await run();
        expect(driver.screenshots).toBe(0);
        expect(driver.calls).toEqual([]);
        expect(JSON.stringify(model.lastResults())).toMatch(/security or password window/);
    });

    it('treats a password manager as never-touch', async () => {
        const { driver, run } = setup([reply([use('screenshot')]), done()], {
            rules: { '1password': { allowed: true } },
            allowedApps: ['1Password'],
        });
        driver.front = { name: '1Password' };
        await run();
        expect(driver.screenshots).toBe(0);
    });
});

describe('approval before send, pay, delete or submit', () => {
    it('holds a click on Send until the card is released, then runs it once', async () => {
        const { driver, approvals, model, run } = setup([
            reply([use('left_click', { coordinate: [100, 50] }), use('key', { text: 'Tab' })]),
            done(),
        ]);
        driver.label = 'Send';
        let clickedBeforeRelease = false;
        approvals.whileWaiting = () => {
            clickedBeforeRelease = driver.calls.length > 0;
        };
        const receipt = await run();
        expect(approvals.proposed).toHaveLength(1);
        expect(approvals.proposed[0]).toMatchObject({ kind: 'send', app: 'Mail' });
        expect(clickedBeforeRelease).toBe(false);
        expect(driver.calls).toEqual([{ name: 'click', args: ['left', [200, 100], 1, []] }]);
        expect(approvals.claims).toHaveLength(1);
        expect(approvals.reports).toEqual([expect.objectContaining({ ok: true })]);
        // The rest of the batch was planned for the old screen: not run.
        expect(JSON.stringify(model.lastResults()[1])).toContain('Not executed');
        expect(receipt.totals.approvals).toBe(1);
    });

    it('binds the card to the exact action: the fingerprint covers app, action and real coordinates', async () => {
        const { driver, approvals, run } = setup([
            reply([use('left_click', { coordinate: [100, 50] })]),
            done(),
        ]);
        driver.label = 'Pay now';
        await run();
        const request = approvals.proposed[0];
        expect(request.kind).toBe('pay');
        expect(request.fingerprint).toBe(
            fingerprint('Mail', { name: 'left_click', input: { coordinate: [200, 100] } }),
        );
        expect(request.fingerprint).not.toBe(
            fingerprint('Mail', { name: 'left_click', input: { coordinate: [202, 100] } }),
        );
    });

    it('does nothing when the person says no', async () => {
        const { driver, approvals, model, run } = setup([
            reply([use('left_click', { coordinate: [100, 50] })]),
            done(),
        ]);
        driver.label = 'Delete';
        approvals.outcome = 'declined';
        const receipt = await run();
        expect(driver.calls).toEqual([]);
        expect(approvals.claims).toEqual([]);
        expect(JSON.stringify(model.lastResults())).toMatch(/said no/);
        expect(receipt.steps.at(-1)?.outcome).toBe('declined');
    });

    it('does not run a step whose claim the server refuses (already used)', async () => {
        const { driver, approvals, run } = setup([
            reply([use('left_click', { coordinate: [100, 50] })]),
            done(),
        ]);
        driver.label = 'Submit';
        approvals.claimResult = false;
        await run();
        expect(driver.calls).toEqual([]);
    });

    it('runs an approved step once even if the model repeats the same click', async () => {
        const click = () => reply([use('left_click', { coordinate: [100, 50] })]);
        const { driver, approvals, run } = setup([click(), click(), done()]);
        driver.label = 'Send';
        await run();
        // Each repeat is a new card: the first approval does not cover the second.
        expect(approvals.proposed).toHaveLength(2);
        expect(driver.calls.filter((c) => c.name === 'click')).toHaveLength(2);
        expect(new Set(approvals.claims.map((c) => c.id)).size).toBe(2);
    });

    it('holds the next action after the model declares it consequential', async () => {
        const { driver, approvals, run } = setup([
            reply([
                use(
                    'request_approval',
                    {
                        kind: 'pay',
                        summary: "Pay Acme Print's invoice: ₹4,800",
                        detail: 'Invoice 23, HDFC ••4821',
                    },
                    false,
                ),
            ]),
            reply([use('left_click', { coordinate: [300, 300] })]),
            done(),
        ]);
        driver.label = undefined; // a button the accessibility tree cannot name
        await run();
        expect(approvals.proposed[0]).toMatchObject({
            kind: 'pay',
            summary: "Pay Acme Print's invoice: ₹4,800",
            detail: 'Invoice 23, HDFC ••4821',
        });
        expect(driver.calls).toHaveLength(1);
    });

    it('treats Enter in a single-line field as a submit, and Cmd+Enter as a send', async () => {
        const { driver, approvals, run } = setup([
            reply([use('key', { text: 'Return' })]),
            reply([use('key', { text: 'cmd+Return' })]),
            done(),
        ]);
        driver.focused = { secure: false, role: 'AXTextField' };
        await run();
        expect(approvals.proposed.map((p) => p.kind)).toEqual(['submit', 'send']);
    });

    it('does not ask for an ordinary click', async () => {
        const { driver, approvals, run } = setup([
            reply([use('left_click', { coordinate: [10, 10] })]),
            done(),
        ]);
        driver.label = 'Inbox';
        await run();
        expect(approvals.proposed).toEqual([]);
        expect(driver.calls).toHaveLength(1);
    });
});

describe('Stop', () => {
    it('stops before the next model call and runs nothing more', async () => {
        const { driver, controller, model, run } = setup([
            (req) => {
                controller.abort();
                return reply([
                    use('left_click', { coordinate: [10, 10] }),
                    use('type', { text: 'x' }),
                ]);
            },
            done(),
        ]);
        const receipt = await run();
        expect(driver.calls).toEqual([]);
        expect(model.requests).toHaveLength(1);
        expect(receipt.endReason).toBe('stopped');
    });

    it('stops in the middle of a batch: the rest is not executed', async () => {
        const { driver, controller, run } = setup([
            reply([
                use('left_click', { coordinate: [10, 10] }),
                use('left_click', { coordinate: [20, 20] }),
            ]),
            done(),
        ]);
        driver.onAction = () => controller.abort();
        const receipt = await run();
        expect(driver.calls).toHaveLength(1);
        expect(receipt.endReason).toBe('stopped');
    });

    it('takes a waiting card off the thread and never runs the step', async () => {
        const { driver, approvals, controller, run } = setup([
            reply([use('left_click', { coordinate: [100, 50] })]),
            done(),
        ]);
        driver.label = 'Send';
        approvals.whileWaiting = () => controller.abort();
        const receipt = await run();
        expect(approvals.cancelled).toEqual([101]);
        expect(approvals.claims).toEqual([]);
        expect(driver.calls).toEqual([]);
        expect(receipt.endReason).toBe('stopped');
    });
});

describe('limits', () => {
    it('stops at the step limit', async () => {
        const many = Array.from({ length: 10 }, () =>
            reply([use('scroll', { scroll_direction: 'down', scroll_amount: 1 })]),
        );
        const { driver, run } = setup(many, { limits: { ...DEFAULT_LIMITS, maxSteps: 3 } });
        const receipt = await run();
        expect(driver.calls).toHaveLength(3);
        expect(receipt.endReason).toBe('limit');
        expect(receipt.endNote).toMatch(/step limit/);
    });

    it('stops at the cost limit, counting model usage', async () => {
        const costly = { input_tokens: 400_000, output_tokens: 10_000 }; // $1.80 at $4/$20
        const { run } = setup(
            [
                reply([use('screenshot')], 'tool_use', costly),
                reply([use('screenshot')], 'tool_use', costly),
                done(),
            ],
            {
                limits: { ...DEFAULT_LIMITS, maxCostUsd: 2 },
            },
        );
        const receipt = await run();
        expect(receipt.endReason).toBe('limit');
        expect(receipt.endNote).toMatch(/cost limit/);
        expect(receipt.totals.costUsd).toBeCloseTo(3.6, 5);
    });

    it('stops at the time limit, not counting time spent waiting for a person', async () => {
        let clock = 0;
        const { driver, approvals, run } = setup(
            [
                reply([use('left_click', { coordinate: [100, 50] })]),
                (r) => {
                    clock += 5_000;
                    return reply([use('screenshot')]);
                },
                (r) => {
                    clock += 120_000;
                    return reply([use('screenshot')]);
                },
                done(),
            ],
            { limits: { ...DEFAULT_LIMITS, maxSeconds: 60 }, now: () => clock },
        );
        driver.label = 'Send';
        // The person takes ten minutes to answer: that is not the agent's time.
        approvals.whileWaiting = () => {
            clock += 600_000;
        };
        const receipt = await run();
        expect(driver.calls).toHaveLength(1);
        expect(receipt.endReason).toBe('limit');
        expect(receipt.endNote).toMatch(/time limit/);
    });
});

describe('the receipt', () => {
    it('records what was done and holds no screenshot', async () => {
        const { run } = setup([
            reply([
                use('screenshot'),
                use('left_click', { coordinate: [10, 10] }),
                use('type', { text: 'Thanks, Asha' }),
            ]),
            done('Replied.'),
        ]);
        const receipt = await run();
        const json = JSON.stringify(receipt);
        expect(json).not.toContain('SCREENSHOT_BYTES');
        expect(receipt.steps.map((s) => s.action)).toEqual(['screenshot', 'left_click', 'type']);
        expect(receipt.steps[2].detail).toContain('Thanks, Asha');
        expect(receipt.summary).toBe('Replied.');
    });

    it('ends honestly when the model refuses', async () => {
        const { run } = setup([reply([{ type: 'text', text: '' }], 'refusal')]);
        const receipt = await run();
        expect(receipt.endReason).toBe('refused');
    });
});
