/** Segments go up in order, are retried, wait out an offline spell, and a
 *  part that cannot be sent is reported -- it becomes a gap, never a skip. */

import { describe, expect, it, vi } from 'vitest';

import { UploadQueue } from '../uploadQueue';

const instant = () => Promise.resolve();

describe('UploadQueue', () => {
    it('sends in order', async () => {
        const sent: number[] = [];
        const queue = new UploadQueue<number>({ send: async (n) => (sent.push(n), 'ok'), sleep: instant });
        queue.enqueue('0', 0);
        queue.enqueue('1', 1);
        queue.enqueue('2', 2);
        expect(await queue.drained(1000)).toBe(true);
        expect(sent).toEqual([0, 1, 2]);
    });

    it('retries a failed send, then gives up and says so', async () => {
        const send = vi.fn(async () => 'retry' as const);
        const gaveUp = vi.fn();
        const queue = new UploadQueue<number>({ send, onGiveUp: gaveUp, sleep: instant, delays: [1, 1] });
        queue.enqueue('0', 0);
        await queue.drained(1000);
        expect(send).toHaveBeenCalledTimes(3);
        expect(gaveUp).toHaveBeenCalledWith(0);
    });

    it('does not retry a refusal', async () => {
        const send = vi.fn(async () => 'refused' as const);
        const gaveUp = vi.fn();
        const queue = new UploadQueue<number>({ send, onGiveUp: gaveUp, sleep: instant });
        queue.enqueue('0', 0);
        await queue.drained(1000);
        expect(send).toHaveBeenCalledTimes(1);
        expect(gaveUp).toHaveBeenCalled();
    });

    it('waits while offline without spending attempts', async () => {
        let online = false;
        let waits = 0;
        const send = vi.fn(async () => 'ok' as const);
        const queue = new UploadQueue<number>({
            send,
            isOnline: () => online,
            sleep: async () => {
                waits += 1;
                if (waits === 3) online = true;
            },
        });
        queue.enqueue('0', 0);
        await queue.drained(1000);
        expect(waits).toBe(3);
        expect(send).toHaveBeenCalledTimes(1);
    });

    it('a thrown network error is retried', async () => {
        let calls = 0;
        const queue = new UploadQueue<number>({
            send: async () => {
                calls += 1;
                if (calls === 1) throw new Error('network');
                return 'ok';
            },
            sleep: instant,
        });
        queue.enqueue('0', 0);
        await queue.drained(1000);
        expect(calls).toBe(2);
    });
});
