/**
 * Confirm once: a double tap, a tap during a slow network, or the same card
 * in the dock and the thread sends one request.
 */
import { createSettler, type SettleRequest } from '@/lib/approvals/confirmOnce';

function card(state: string) {
    return { id: 41, at: '', kind: 'action_proposed', actor: 'agent', summary: '', payload: { state }, is_deliverable: false, workflow_id: null, workflow_run_id: null, folder_id: null };
}

function slowServer() {
    const requests: SettleRequest[] = [];
    let release: () => void = () => undefined;
    const gate = new Promise<void>((resolve) => {
        release = resolve;
    });
    const settle = jest.fn(async (request: SettleRequest) => {
        requests.push(request);
        await gate;
        return card(request.verb === 'confirm' ? 'armed' : 'declined');
    });
    return { settle, requests, release: () => release() };
}

test('a double tap on Confirm sends one request and both taps get its answer', async () => {
    const server = slowServer();
    const settler = createSettler(server.settle);
    const first = settler.answer(41, 'confirm', 'v1');
    const second = settler.answer(41, 'confirm', 'v1');
    expect(settler.pending(41)).toBe(true);
    server.release();
    const [a, b] = await Promise.all([first, second]);
    expect(server.settle).toHaveBeenCalledTimes(1);
    expect(a).toBe(b);
    expect(a.payload.state).toBe('armed');
    expect(settler.pending(41)).toBe(false);
});

test('Do it and Don\'t pressed together: only the first is sent', async () => {
    const server = slowServer();
    const settler = createSettler(server.settle);
    const confirm = settler.answer(41, 'confirm', 'v1');
    const decline = settler.answer(41, 'decline');
    server.release();
    await Promise.all([confirm, decline]);
    expect(server.requests).toEqual([{ event_id: 41, verb: 'confirm', version: 'v1' }]);
});

test('after a Confirm succeeded, Confirm again is answered without a request', async () => {
    const settle = jest.fn(async () => card('armed'));
    const settler = createSettler(settle);
    await settler.answer(41, 'confirm', 'v1');
    await settler.answer(41, 'confirm', 'v1');
    expect(settle).toHaveBeenCalledTimes(1);
});

test('Confirm names the version the person read; Decline does not need one', async () => {
    const settle = jest.fn(async () => card('armed'));
    const settler = createSettler(settle);
    await settler.answer(41, 'confirm', 'abc123');
    await settler.answer(42, 'decline', 'abc123');
    expect(settle.mock.calls).toEqual([[{ event_id: 41, verb: 'confirm', version: 'abc123' }], [{ event_id: 42, verb: 'decline' }]]);
});

test('Undo can follow a Confirm', async () => {
    const settle = jest.fn(async (r: SettleRequest) => card(r.verb === 'undo' ? 'cancelled' : 'armed'));
    const settler = createSettler(settle);
    await settler.answer(41, 'confirm', 'v1');
    const undone = await settler.answer(41, 'undo');
    expect(undone.payload.state).toBe('cancelled');
    expect(settle).toHaveBeenCalledTimes(2);
});

test('a failed request is forgotten so the person can try again', async () => {
    const settle = jest
        .fn()
        .mockRejectedValueOnce(new Error('offline'))
        .mockResolvedValueOnce(card('armed'));
    const settler = createSettler(settle);
    await expect(settler.answer(41, 'confirm', 'v1')).rejects.toThrow('offline');
    await expect(settler.answer(41, 'confirm', 'v1')).resolves.toMatchObject({ payload: { state: 'armed' } });
    expect(settle).toHaveBeenCalledTimes(2);
});
