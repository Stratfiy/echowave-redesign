/** The small pure pieces: phone numbers, links, turn status, PDF, i18n. */
import { mergeRows, type Row } from '@/lib/chat/events';
import { jpegToPdf, readJpeg } from '@/lib/chat/pdf';
import { latestTurnStatus } from '@/lib/chat/turnStatus';
import { en } from '@/lib/i18n/en';
import { hi } from '@/lib/i18n/hi';
import { localeFor, translate } from '@/lib/i18n';
import { routeForPath, routeForUrl } from '@/lib/links';
import { displayPhone, toE164 } from '@/lib/phone';
import { bootstrapHtml, safeWebPath, WEB_SCREENS } from '@/lib/webScreens';

describe('E.164', () => {
    test.each([
        ['9876543210', '+919876543210'],
        ['09876543210', '+919876543210'],
        ['+91 98765-43210', '+919876543210'],
        ['919876543210', '+919876543210'],
        ['0091 98765 43210', '+919876543210'],
        ['080 1234 5678', '+918012345678'],
        ['+1 (415) 555-0100', '+14155550100'],
        ['98765 43210 ext. 22', '+919876543210'],
        ['12345', null],
        ['', null],
        [null, null],
    ])('%s -> %s', (raw, e164) => {
        expect(toE164(raw as string)).toBe(e164);
    });
    test('display keeps Indian grouping', () => expect(displayPhone('+919876543210')).toBe('+91 98765 43210'));
});

describe('deep links', () => {
    test.each([
        ['decibyl://chat/abc-123', '/chat/abc-123'],
        ['decibyl://thread/abc', '/chat/abc'],
        ['decibyl://approvals/42', '/approvals/42'],
        ['decibyl://reminders/7', '/reminders/7'],
        ['decibyl://today', '/today'],
        ['decibyl://ask?q=Call%20Asha', '/chat/new?q=Call%20Asha'],
        ['https://app.decibyl.ai/overview?thread=t1', '/chat/t1'],
        ['https://app.decibyl.ai/tasks/approvals/42', '/approvals/42'],
        ['https://app.decibyl.ai/tasks/reminders/new', '/reminders/new'],
        ['https://app.decibyl.ai/tasks', '/today'],
        ['https://app.decibyl.ai/settings/memory', '/web?path=%2Fsettings%2Fmemory'],
        ['https://evil.example.com/overview?thread=t1', null],
        ['decibyl://chat/../../etc', '/'],
    ])('%s -> %s', (url, route) => {
        expect(routeForUrl(url)).toBe(route);
    });
    test('push notification paths', () => {
        expect(routeForPath('/overview?thread=abc')).toBe('/chat/abc');
        expect(routeForPath('/tasks/approvals/77')).toBe('/approvals/77');
        expect(routeForPath('/settings/notifications')).toBe('/settings/notifications');
        expect(routeForPath('/')).toBe('/');
    });
});

describe('turn status', () => {
    const rows = (extra: Partial<Row>[]): Row[] =>
        [{ id: 1, at: '1', kind: 'message', actor: 'human', summary: 'hi', payload: {} }, ...extra].map(
            (r, i) => ({ id: i + 1, at: String(i + 1), kind: 'message', actor: 'agent', summary: '', payload: {}, ...r }) as Row,
        );
    test('waiting with no reply is running, with the stage', () => {
        expect(latestTurnStatus(rows([{ kind: 'activity', summary: 'Read your files' }]), true)).toEqual({
            state: 'running',
            requestId: 1,
            stage: 'Read your files',
        });
    });
    test('a card waiting is awaiting approval; a connect chip is needs input', () => {
        expect(latestTurnStatus(rows([{ kind: 'action_proposed', payload: { state: 'proposed' } }]), false)?.state).toBe('awaiting_approval');
        expect(latestTurnStatus(rows([{ kind: 'connector_offered' }]), false)?.state).toBe('needs_input');
    });
    test('stopped is partial, failed is failed, otherwise done', () => {
        expect(latestTurnStatus(rows([{ payload: { stopped: true } }]), false)?.state).toBe('partial');
        expect(latestTurnStatus(rows([{ payload: { failed: true } }]), false)?.state).toBe('failed');
        expect(latestTurnStatus(rows([{}]), false)?.state).toBe('completed');
    });
    test('a refetch never duplicates a line', () => {
        const a = { id: 1, at: '2026-10-07T10:00:00Z', kind: 'message', actor: 'human', summary: 'a', payload: {} } as Row;
        const b = { id: 2, at: '2026-10-07T10:00:01Z', kind: 'message', actor: 'agent', summary: 'b', payload: {} } as Row;
        expect(mergeRows([a], [b, a]).map((r) => r.id)).toEqual([1, 2]);
    });
});

const latin1 = (bytes: Uint8Array) => Array.from(bytes, (b) => String.fromCharCode(b)).join('');

describe('a photo as a one-page PDF', () => {
    // The smallest valid baseline JPEG header: SOI, SOF0 (8x4, 3 components), EOI.
    const jpeg = new Uint8Array([
        0xff, 0xd8, 0xff, 0xe0, 0x00, 0x04, 0x00, 0x00, 0xff, 0xc0, 0x00, 0x11, 0x08, 0x00, 0x04, 0x00, 0x08, 0x03,
        0x01, 0x22, 0x00, 0x02, 0x11, 0x01, 0x03, 0x11, 0x01, 0xff, 0xd9,
    ]);
    test('reads the size from the JPEG', () => expect(readJpeg(jpeg)).toEqual({ width: 8, height: 4, components: 3 }));
    test('the PDF embeds the JPEG untouched with a correct cross-reference table', () => {
        const pdf = jpegToPdf(jpeg);
        const text = latin1(pdf);
        expect(text.startsWith('%PDF-1.4')).toBe(true);
        expect(text).toContain('/Filter /DCTDecode');
        expect(text).toContain('/Width 8 /Height 4');
        expect(text.indexOf(latin1(jpeg))).toBeGreaterThan(0);
        // Every xref offset points at "n 0 obj".
        const xrefAt = Number(/startxref\n(\d+)/.exec(text)?.[1]);
        expect(text.slice(xrefAt, xrefAt + 4)).toBe('xref');
        const offsets = [...text.slice(xrefAt).matchAll(/(\d{10}) 00000 n/g)].map((m) => Number(m[1]));
        offsets.forEach((offset, i) => expect(text.slice(offset).startsWith(`${i + 1} 0 obj`)).toBe(true));
    });
    test('not a JPEG is refused in words', () => expect(() => jpegToPdf(new Uint8Array([1, 2, 3]))).toThrow('could not be read'));
});

describe('Hindi and English', () => {
    test('every English key has a Hindi string with the same placeholders', () => {
        for (const key of Object.keys(en) as (keyof typeof en)[]) {
            expect(hi[key]).toBeTruthy();
            const holes = (s: string) => (s.match(/\{\w+\}/g) ?? []).sort();
            expect(holes(hi[key])).toEqual(holes(en[key]));
        }
        expect(Object.keys(hi).sort()).toEqual(Object.keys(en).sort());
    });
    test('the language follows the preference tag', () => {
        expect(localeFor('hi-IN')).toBe('hi');
        expect(localeFor('ta-IN')).toBe('en');
        expect(translate('hi', 'tabs.today')).toBe('आज');
        expect(translate('en', 'chat.threads.messages', { count: 3 })).toBe('3 messages');
    });
    test('no price, plan or positioning line in the app strings', () => {
        const all = [...Object.values(en), ...Object.values(hi)].join(' ');
        expect(all).not.toMatch(/₹|\$\d|per month|\/month|pricing|Free plan|Pro plan|upgrade/i);
    });
});

describe('web views', () => {
    test('only paths of the web app itself', () => {
        expect(safeWebPath('/settings/memory')).toBe('/settings/memory');
        expect(safeWebPath('https://evil.example.com')).toBe('/');
        expect(safeWebPath('//evil.example.com')).toBe('/');
    });
    test('the token goes in a same-origin POST body, never the address', () => {
        const html = bootstrapHtml('jwt.token', { id: 1 }, '/settings/memory');
        expect(html).toContain("fetch('/api/auth/session'");
        expect(html).toContain('"token":"jwt.token"');
        expect(html).toContain('location.replace("/settings/memory")');
        expect(html).not.toMatch(/\?token=/);
    });
    test('every web screen is an app path, listed once', () => {
        const paths = WEB_SCREENS.map((s) => s.path);
        expect(new Set(paths).size).toBe(paths.length);
        paths.forEach((p) => expect(safeWebPath(p)).toBe(p));
    });
});
