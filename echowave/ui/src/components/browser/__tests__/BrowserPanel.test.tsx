/**
 * The private browser's panel in the thread.
 *
 * Guarded: each state reads as itself (a CAPTCHA is not "Working"); the live
 * view shows the page; Take over turns clicks on the picture into clicks on
 * the page at the right place and sends typing without keeping it; Hand
 * back can keep the login; the receipt shows what it did, what it would not
 * do and the links; somebody else's browser says whose it is.
 */

import { fireEvent, render, screen, waitFor } from '@testing-library/react';
import React from 'react';
import { beforeEach, describe, expect, it, vi } from 'vitest';

const api = vi.hoisted(() => ({
    getSession: vi.fn(),
    getScreen: vi.fn(),
    takeOver: vi.fn(),
    input: vi.fn(),
    handBack: vi.fn(),
    stop: vi.fn(),
    logins: vi.fn(),
    forget: vi.fn(),
}));
const feature = vi.hoisted(() => ({ on: true }));

vi.mock('@/client/sdk.gen', () => ({
    getBrowserSessionApiV1BrowserSessionsSessionUuidGet: api.getSession,
    getBrowserScreenApiV1BrowserSessionsSessionUuidScreenGet: api.getScreen,
    takeOverBrowserApiV1BrowserSessionsSessionUuidTakeoverPost: api.takeOver,
    sendBrowserInputApiV1BrowserSessionsSessionUuidInputPost: api.input,
    handBackBrowserApiV1BrowserSessionsSessionUuidHandbackPost: api.handBack,
    stopBrowserApiV1BrowserSessionsSessionUuidStopPost: api.stop,
    listBrowserLoginsApiV1BrowserLoginsGet: api.logins,
    deleteBrowserLoginApiV1BrowserLoginsLoginIdDelete: api.forget,
}));
vi.mock('@/lib/auth', () => ({ useAuth: () => ({ user: { id: 1 }, loading: false }) }));
vi.mock('@/lib/features', () => ({ useFeature: () => feature.on }));

import { BrowserPanel, stateOf } from '../BrowserPanel';

const view = (over: Record<string, unknown> = {}) => ({
    session_uuid: 'u1',
    task: 'Check my BESCOM bill',
    sites: ['bescom.co.in'],
    allowed_verbs: [],
    state: 'working',
    state_note: 'Working…',
    limits: { steps: 25, minutes: 10, cost_paise: 1500 },
    used: { steps: 3, minutes: 1, cost_paise: 40 },
    steps: [
        { at: '2026-10-07T10:00:00Z', kind: 'step', text: 'Open the bill page' },
        { at: '2026-10-07T10:00:05Z', kind: 'refused', text: '“Pay now” would pay, and you did not ask for that.' },
    ],
    pending_label: null,
    pending_event_id: null,
    keep_login_sites: [],
    receipt: null,
    can_keep_logins: true,
    created_at: '2026-10-07T10:00:00Z',
    ended_at: null,
    ...over,
});

const pixel = { jpeg: 'AAAA', w: 1280, h: 800, url: 'https://www.bescom.co.in/bill', title: 'Your bill', at: 'x' };

beforeEach(() => {
    Object.values(api).forEach((fn) => fn.mockReset());
    feature.on = true;
    api.getScreen.mockResolvedValue({ data: pixel });
    api.takeOver.mockResolvedValue({ data: { ok: true } });
    api.input.mockResolvedValue({ data: { ok: true } });
    api.handBack.mockResolvedValue({ data: { ok: true } });
    api.stop.mockResolvedValue({ data: { ok: true } });
});

describe('states', () => {
    it('names each one honestly, and an unknown one as itself', () => {
        expect(stateOf('captcha').label).toBe('Blocked by a CAPTCHA');
        expect(stateOf('waiting_for_you').label).toBe('Waiting for you');
        expect(stateOf('limit_reached').label).toBe('Stopped at its limit');
        expect(stateOf('something_new').label).toBe('something new');
    });

    it('shows a CAPTCHA as blocked, with Take over to the front', async () => {
        api.getSession.mockResolvedValue({ data: view({ state: 'captcha', state_note: 'Blocked by a CAPTCHA. Take over to solve it, then hand back.' }) });
        render(<BrowserPanel sessionUuid="u1" />);
        expect((await screen.findByTestId('browser-state')).textContent).toContain('Blocked by a CAPTCHA');
        expect(screen.getByRole('button', { name: /Take over/ })).toBeTruthy();
        expect(screen.getByText(/Take over to solve it/)).toBeTruthy();
    });
});

describe('live view and Take over', () => {
    it('shows the page, the steps and the limits', async () => {
        api.getSession.mockResolvedValue({ data: view() });
        render(<BrowserPanel sessionUuid="u1" />);
        const img = (await screen.findByTestId('browser-screen')) as HTMLImageElement;
        expect(img.src).toContain('data:image/jpeg;base64,AAAA');
        expect(screen.getByText('bescom.co.in')).toBeTruthy();
        expect(screen.getByText(/Step 3 of 25/)).toBeTruthy();
        expect(screen.getByText(/would pay, and you did not ask/)).toBeTruthy();
    });

    it('takes over, clicks where the person clicked, types without keeping it, hands back', async () => {
        api.getSession.mockResolvedValue({ data: view({ state: 'taken_over' }) });
        render(<BrowserPanel sessionUuid="u1" />);
        const img = (await screen.findByTestId('browser-screen')) as HTMLImageElement;
        img.getBoundingClientRect = () => ({ left: 0, top: 0, width: 640, height: 400, right: 640, bottom: 400, x: 0, y: 0, toJSON: () => ({}) });
        fireEvent.click(img, { clientX: 320, clientY: 100 });
        await waitFor(() => expect(api.input).toHaveBeenCalled());
        expect(api.input.mock.calls[0][0].body).toEqual({ kind: 'click', x: 640, y: 200 });

        const box = screen.getByLabelText('Type into the page') as HTMLInputElement;
        expect(box.type).toBe('password');
        fireEvent.change(box, { target: { value: 'hunter22' } });
        fireEvent.click(screen.getByRole('button', { name: 'Type it' }));
        await waitFor(() => expect(api.input).toHaveBeenCalledTimes(2));
        expect(api.input.mock.calls[1][0].body).toEqual({ kind: 'type', text: 'hunter22' });
        expect(box.value).toBe('');

        fireEvent.click(screen.getByRole('button', { name: 'Enter' }));
        await waitFor(() => expect(api.input).toHaveBeenCalledTimes(3));
        expect(api.input.mock.calls[2][0].body).toEqual({ kind: 'key', key: 'Enter' });

        fireEvent.click(screen.getByLabelText(/Keep me signed in to bescom.co.in/));
        fireEvent.click(screen.getByRole('button', { name: 'Hand back' }));
        await waitFor(() => expect(api.handBack).toHaveBeenCalled());
        expect(api.handBack.mock.calls[0][0]).toEqual({ path: { session_uuid: 'u1' }, body: { keep_login: true } });
    });

    it('does nothing with a click on the picture until the person has taken over', async () => {
        api.getSession.mockResolvedValue({ data: view() });
        render(<BrowserPanel sessionUuid="u1" />);
        fireEvent.click(await screen.findByTestId('browser-screen'), { clientX: 10, clientY: 10 });
        expect(api.input).not.toHaveBeenCalled();
        fireEvent.click(screen.getByRole('button', { name: /Take over/ }));
        await waitFor(() => expect(api.takeOver).toHaveBeenCalledWith({ path: { session_uuid: 'u1' } }));
    });

    it('says when the press was not taken', async () => {
        api.getSession.mockResolvedValue({ data: view() });
        api.stop.mockResolvedValue({ error: { detail: 'This browser has closed.' } });
        render(<BrowserPanel sessionUuid="u1" />);
        fireEvent.click(await screen.findByRole('button', { name: /Stop/ }));
        expect((await screen.findByRole('alert')).textContent).toContain('This browser has closed.');
    });
});

describe('the receipt', () => {
    it('says what it found, did and would not do, with the links', async () => {
        api.getSession.mockResolvedValue({
            data: view({
                state: 'done',
                state_note: 'Finished.',
                receipt: {
                    state: 'done',
                    summary: 'Your bill is ₹2,340, due 14 Oct.',
                    done: [{ label: 'Submit: press “Get bill” on bescom.co.in', note: 'Pressed.', ok: true }],
                    refused: ['“Pay now” would pay, and you did not ask for that. Not done.'],
                    links: ['https://www.bescom.co.in/bill'],
                    kept_logins: ['bescom.co.in'],
                },
            }),
        });
        render(<BrowserPanel sessionUuid="u1" />);
        const receipt = await screen.findByTestId('browser-receipt');
        expect(receipt.textContent).toContain('Your bill is ₹2,340, due 14 Oct.');
        expect(receipt.textContent).toContain('What it did');
        expect(receipt.textContent).toContain('What it would not do');
        expect(receipt.textContent).toContain('Kept you signed in to bescom.co.in.');
        const link = screen.getByRole('link', { name: /bescom.co.in/ });
        expect(link.getAttribute('rel')).toContain('noopener');
        expect(screen.queryByTestId('browser-screen')).toBeNull();
        expect(screen.queryByRole('button', { name: /Take over/ })).toBeNull();
    });
});

describe('whose it is', () => {
    it('tells anyone else it belongs to the person who asked', async () => {
        api.getSession.mockResolvedValue({ error: { detail: 'Browser session not found' }, response: { status: 404 } });
        render(<BrowserPanel sessionUuid="u1" />);
        expect(await screen.findByText(/belongs to the person who asked for it/)).toBeTruthy();
    });

    it('says it is switched off rather than pretending it belongs to someone', async () => {
        feature.on = false;
        api.getSession.mockResolvedValue({ error: { detail: 'Not Found' }, response: { status: 404 } });
        render(<BrowserPanel sessionUuid="u1" />);
        expect(await screen.findByText(/switched off here/)).toBeTruthy();
    });
});

describe('saved logins', () => {
    it('lists the person’s own and forgets one', async () => {
        api.getSession.mockResolvedValue({ data: view() });
        api.logins.mockResolvedValue({
            data: { logins: [{ id: 7, site: 'bescom.co.in', cookie_count: 3, updated_at: 'x' }], can_keep_logins: true },
        });
        api.forget.mockResolvedValue({ data: { ok: true } });
        const { container } = render(<BrowserPanel sessionUuid="u1" />);
        await screen.findByTestId('browser-screen');
        const details = Array.from(container.querySelectorAll('details')).find((d) =>
            d.textContent?.includes('Saved logins'),
        ) as HTMLDetailsElement;
        details.open = true;
        fireEvent(details, new Event('toggle'));
        fireEvent.click(await screen.findByRole('button', { name: 'Forget bescom.co.in' }));
        await waitFor(() => expect(api.forget).toHaveBeenCalledWith({ path: { login_id: 7 } }));
        await waitFor(() => expect(screen.queryByRole('button', { name: 'Forget bescom.co.in' })).toBeNull());
    });
});
