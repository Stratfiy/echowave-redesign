import { describe, expect, it } from 'vitest';

import {
    computerUseAvailable,
    httpApprovals,
    postReceipt,
} from '../src/computer-use/approvals-http';

type Call = { url: string; method: string; body: unknown; auth: string | null };

function fakeFetch(responses: Array<{ status: number; body?: unknown }>) {
    const calls: Call[] = [];
    const impl = (async (url: string, init: RequestInit = {}) => {
        const headers = (init.headers ?? {}) as Record<string, string>;
        calls.push({
            url,
            method: init.method ?? 'GET',
            body: init.body ? JSON.parse(String(init.body)) : undefined,
            auth: headers.authorization ?? null,
        });
        const next = responses.shift() ?? { status: 200, body: {} };
        return new Response(JSON.stringify(next.body ?? {}), { status: next.status });
    }) as unknown as typeof fetch;
    return { calls, impl };
}

const SESSION = { apiBase: 'https://api.decibyl.ai', token: 'tok', threadId: 't-1' };
const REQUEST = {
    kind: 'send' as const,
    app: 'Mail',
    summary: 'Send the reply to Asha',
    detail: 'To asha@example.com: "Thanks, see you Monday."',
    step: { name: 'left_click', input: { coordinate: [200, 100] } },
    fingerprint: 'a'.repeat(64),
};
const noSleep = async () => undefined;

describe('the approval card over HTTP', () => {
    it('proposes with text only: no coordinates, no screenshot', async () => {
        const { calls, impl } = fakeFetch([{ status: 200, body: { event_id: 7 } }]);
        const client = httpApprovals({
            session: () => SESSION,
            sessionId: 's',
            device: 'mac',
            fetchImpl: impl,
        });
        expect(await client.propose(REQUEST)).toEqual({ id: 7 });
        expect(calls[0].url).toBe('https://api.decibyl.ai/api/v1/desktop/steps');
        expect(calls[0].auth).toBe('Bearer tok');
        expect(calls[0].body).toMatchObject({
            kind: 'send',
            app: 'Mail',
            fingerprint: 'a'.repeat(64),
            step: { name: 'left_click' },
            thread_id: 't-1',
        });
        expect(JSON.stringify(calls[0].body)).not.toContain('coordinate');
    });

    it('waits through proposed and armed, and maps the end states', async () => {
        const states = (end: string) => [
            { status: 200, body: { state: 'proposed' } },
            { status: 200, body: { state: 'armed' } },
            { status: 200, body: { state: end } },
        ];
        for (const [end, outcome] of [
            ['released', 'released'],
            ['declined', 'declined'],
            ['cancelled', 'cancelled'],
            ['failed', 'unavailable'],
        ]) {
            const { impl } = fakeFetch(states(end));
            const client = httpApprovals({
                session: () => SESSION,
                sessionId: 's',
                device: 'mac',
                fetchImpl: impl,
                sleep: noSleep,
            });
            expect(await client.waitForDecision(7, new AbortController().signal)).toBe(outcome);
        }
    });

    it('takes an unanswered card off after the timeout', async () => {
        const { calls, impl } = fakeFetch([
            { status: 200, body: { state: 'proposed' } },
            { status: 200, body: {} },
        ]);
        const client = httpApprovals({
            session: () => SESSION,
            sessionId: 's',
            device: 'mac',
            fetchImpl: impl,
            sleep: noSleep,
            timeoutMs: 0,
        });
        expect(await client.waitForDecision(7, new AbortController().signal)).toBe('expired');
        expect(calls.at(-1)?.url).toMatch(/\/steps\/7\/cancel$/);
    });

    it('stops waiting when Stop is pressed', async () => {
        const controller = new AbortController();
        controller.abort();
        const { impl } = fakeFetch([]);
        const client = httpApprovals({
            session: () => SESSION,
            sessionId: 's',
            device: 'mac',
            fetchImpl: impl,
            sleep: noSleep,
        });
        expect(await client.waitForDecision(7, controller.signal)).toBe('cancelled');
    });

    it('claims once: a 409 is a refusal, not an error', async () => {
        const { impl } = fakeFetch([{ status: 200, body: { claimed: true } }, { status: 409 }]);
        const client = httpApprovals({
            session: () => SESSION,
            sessionId: 's',
            device: 'mac',
            fetchImpl: impl,
        });
        expect(await client.claim(7, 'a'.repeat(64))).toBe(true);
        expect(await client.claim(7, 'a'.repeat(64))).toBe(false);
    });

    it('cannot propose while signed out', async () => {
        const client = httpApprovals({
            session: () => null,
            sessionId: 's',
            device: 'mac',
            fetchImpl: fakeFetch([]).impl,
        });
        await expect(client.propose(REQUEST)).rejects.toThrow(/Not signed in/);
    });
});

describe('the switch and the receipt', () => {
    it('reads the switch from /desktop/status (404 while off)', async () => {
        expect(await computerUseAvailable(SESSION, fakeFetch([{ status: 404 }]).impl)).toBe(false);
        expect(await computerUseAvailable(SESSION, fakeFetch([{ status: 200 }]).impl)).toBe(true);
        expect(await computerUseAvailable(null, fakeFetch([]).impl)).toBe(false);
    });

    it('posts the receipt as text to the thread', async () => {
        const { calls, impl } = fakeFetch([{ status: 200 }]);
        expect(await postReceipt(SESSION, 'Worked on your computer: x', impl)).toBe(true);
        expect(calls[0].body).toEqual({ text: 'Worked on your computer: x', thread_id: 't-1' });
    });
});
