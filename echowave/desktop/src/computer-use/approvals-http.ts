/**
 * The approval card, from this side: api/routes/desktop.py.
 *
 * propose -> poll until the card leaves proposed/armed -> claim once ->
 * report. Only text goes over this wire: the sentence, the detail, the
 * app's name and the step's fingerprint. Never a screenshot.
 */

import { randomUUID } from 'node:crypto';

import type { ApprovalClient, ApprovalOutcome, ApprovalRequest } from './types';

export interface Session {
    apiBase: string;
    token: string;
    threadId?: string | null;
}

export interface HttpApprovalOptions {
    session: () => Session | null;
    sessionId: string;
    device: string;
    fetchImpl?: typeof fetch;
    pollMs?: number;
    /** A card nobody answers is taken off after this long. */
    timeoutMs?: number;
    sleep?: (ms: number, signal: AbortSignal) => Promise<void>;
}

export class ApprovalUnavailable extends Error {}

const defaultSleep = (ms: number, signal: AbortSignal) =>
    new Promise<void>((resolve) => {
        const timer = setTimeout(resolve, ms);
        signal.addEventListener('abort', () => {
            clearTimeout(timer);
            resolve();
        });
    });

export function httpApprovals(opts: HttpApprovalOptions): ApprovalClient {
    const fetchImpl = opts.fetchImpl ?? fetch;
    const pollMs = opts.pollMs ?? 1500;
    const timeoutMs = opts.timeoutMs ?? 10 * 60 * 1000;
    const sleep = opts.sleep ?? defaultSleep;

    const call = async (method: string, path: string, body?: unknown): Promise<Response> => {
        const session = opts.session();
        if (!session) throw new ApprovalUnavailable('Not signed in to Decibyl in this app.');
        return fetchImpl(`${session.apiBase.replace(/\/$/, '')}/api/v1/desktop${path}`, {
            method,
            headers: {
                'content-type': 'application/json',
                authorization: `Bearer ${session.token}`,
            },
            body: body === undefined ? undefined : JSON.stringify(body),
        });
    };

    const client: ApprovalClient = {
        async propose(request: ApprovalRequest) {
            const response = await call('POST', '/steps', {
                kind: request.kind,
                app: request.app,
                summary: request.summary,
                detail: request.detail,
                fingerprint: request.fingerprint,
                step: { name: request.step.name },
                session_id: opts.sessionId,
                request_id: randomUUID(),
                device: opts.device,
                thread_id: opts.session()?.threadId ?? null,
            });
            if (!response.ok)
                throw new ApprovalUnavailable(
                    `The approval card could not be made (${response.status}).`,
                );
            const data = (await response.json()) as { event_id: number };
            return { id: data.event_id };
        },

        async waitForDecision(id: number, signal: AbortSignal): Promise<ApprovalOutcome> {
            const deadline = Date.now() + timeoutMs;
            while (!signal.aborted) {
                const response = await call('GET', `/steps/${id}`).catch(() => null);
                if (response?.ok) {
                    const { state } = (await response.json()) as { state: string };
                    if (state === 'released') return 'released';
                    if (state === 'declined') return 'declined';
                    if (state === 'cancelled' || state === 'undone') return 'cancelled';
                    if (state !== 'proposed' && state !== 'armed') return 'unavailable';
                } else if (response && response.status === 404) {
                    return 'unavailable';
                }
                if (Date.now() >= deadline) {
                    await client.cancel(id).catch(() => undefined);
                    return 'expired';
                }
                await sleep(pollMs, signal);
            }
            return 'cancelled';
        },

        async claim(id: number, fingerprint: string) {
            const response = await call('POST', `/steps/${id}/claim`, { fingerprint });
            return response.ok;
        },

        async report(id: number, ok: boolean | null, note: string) {
            await call('POST', `/steps/${id}/outcome`, { ok, note: note.slice(0, 600) });
        },

        async cancel(id: number) {
            await call('POST', `/steps/${id}/cancel`);
        },
    };
    return client;
}

/** Whether the switch is on for this person's organisation. */
export async function computerUseAvailable(
    session: Session | null,
    fetchImpl: typeof fetch = fetch,
): Promise<boolean> {
    if (!session) return false;
    try {
        const response = await fetchImpl(
            `${session.apiBase.replace(/\/$/, '')}/api/v1/desktop/status`,
            {
                headers: { authorization: `Bearer ${session.token}` },
            },
        );
        return response.ok;
    } catch {
        return false;
    }
}

export async function postReceipt(
    session: Session | null,
    text: string,
    fetchImpl: typeof fetch = fetch,
): Promise<boolean> {
    if (!session) return false;
    try {
        const response = await fetchImpl(
            `${session.apiBase.replace(/\/$/, '')}/api/v1/desktop/receipts`,
            {
                method: 'POST',
                headers: {
                    'content-type': 'application/json',
                    authorization: `Bearer ${session.token}`,
                },
                body: JSON.stringify({
                    text: text.slice(0, 4000),
                    thread_id: session.threadId ?? null,
                }),
            },
        );
        return response.ok;
    } catch {
        return false;
    }
}
