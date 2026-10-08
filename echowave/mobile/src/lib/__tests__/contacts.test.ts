/**
 * Contact sync: what leaves the phone, what counts as a change, and that
 * nothing leaves without consent.
 */
import { CONSENT_KEY, CONSENT_VERSION, giveConsent, hasConsent } from '@/lib/contacts/consent';
import { diffContacts, normalizeAll, normalizeContact, type DeviceContact } from '@/lib/contacts/diff';
import { LAST_SYNC_KEY, SNAPSHOT_KEY, stopSyncing, syncContacts, type ContactSource, type Permission } from '@/lib/contacts/sync';
import { DevicePeopleApi, type PeopleApi } from '@/lib/people/api';
import { memoryStore } from '@/lib/storage';

describe('normalising', () => {
    test('the same number typed four ways is one E.164 number', () => {
        const r = normalizeContact({
            id: '1',
            name: '  Asha   Verma ',
            phones: ['098765 43210', '+91 98765-43210', '9876543210', '0091 98765 43210'],
            emails: ['ASHA@Example.com ', 'asha@example.com', 'not-an-email'],
        });
        expect(r).toMatchObject({ device_id: '1', name: 'Asha Verma', phones: ['+919876543210'], emails: ['asha@example.com'] });
    });

    test('a contact with nothing to know them by is not a person record', () => {
        expect(normalizeContact({ id: '2', name: 'Just a name', phones: ['12'], emails: [] })).toBeNull();
    });

    test('no name: the number stands in', () => {
        expect(normalizeContact({ id: '3', phones: ['9123456789'] })?.name).toBe('+919123456789');
    });

    test('reformatting a number is not a change; a new number is', () => {
        const a = normalizeContact({ id: '1', name: 'A', phones: ['09876543210'] });
        const b = normalizeContact({ id: '1', name: 'A', phones: ['+91 98765 43210'] });
        const c = normalizeContact({ id: '1', name: 'A', phones: ['+91 98765 43211'] });
        expect(a?.fingerprint).toBe(b?.fingerprint);
        expect(a?.fingerprint).not.toBe(c?.fingerprint);
    });

    test('duplicate device ids are kept once', () => {
        const all = normalizeAll([
            { id: '1', name: 'A', phones: ['9876543210'] },
            { id: '1', name: 'A again', phones: ['9876543210'] },
        ]);
        expect(all).toHaveLength(1);
    });
});

describe('diffing', () => {
    const base: DeviceContact[] = [
        { id: '1', name: 'Asha', phones: ['9876543210'] },
        { id: '2', name: 'Rahul', phones: ['9123456789'] },
        { id: '3', name: 'Priya', emails: ['priya@example.com'] },
    ];

    test('first sync: everything is added', () => {
        const d = diffContacts({}, normalizeAll(base));
        expect(d.added.map((r) => r.device_id)).toEqual(['1', '2', '3']);
        expect(d.changed).toEqual([]);
        expect(d.removed).toEqual([]);
    });

    test('second sync: only what changed, what was added and what went', () => {
        const first = diffContacts({}, normalizeAll(base));
        const now = normalizeAll([
            { id: '1', name: 'Asha', phones: ['+91 98765 43210'] }, // same number, new shape
            { id: '2', name: 'Rahul Mehta', phones: ['9123456789'] }, // renamed
            { id: '4', name: 'Sunita', phones: ['9988776655'] }, // new
        ]);
        const second = diffContacts(first.next, now);
        expect(second.added.map((r) => r.device_id)).toEqual(['4']);
        expect(second.changed.map((r) => r.device_id)).toEqual(['2']);
        expect(second.removed).toEqual(['3']);
        expect(second.unchanged).toBe(1);
        expect(Object.keys(second.next).sort()).toEqual(['1', '2', '4']);
    });
});

function source(contacts: DeviceContact[], permission: Permission = 'granted'): ContactSource & { reads: number } {
    const s = {
        reads: 0,
        available: () => true,
        permission: async () => permission,
        request: async () => permission,
        read: async () => {
            s.reads += 1;
            return contacts;
        },
    };
    return s;
}

function recordingPeople(): PeopleApi & { upserts: string[][]; removes: string[][] } {
    const calls = { upserts: [] as string[][], removes: [] as string[][] };
    return {
        kind: 'server',
        ...calls,
        upsert: async (records) => void calls.upserts.push(records.map((r) => r.device_id)),
        remove: async (ids) => void calls.removes.push(ids),
        removeAll: async () => undefined,
        list: async () => [],
        get: async () => null,
    } as PeopleApi & { upserts: string[][]; removes: string[][] };
}

describe('syncing', () => {
    const contacts = [
        { id: '1', name: 'Asha', phones: ['9876543210'] },
        { id: '2', name: 'Rahul', phones: ['9123456789'] },
    ];

    test('without consent nothing is read and nothing is sent', async () => {
        const store = memoryStore();
        const src = source(contacts);
        const people = recordingPeople();
        expect(await syncContacts({ source: src, people, store })).toEqual({ status: 'no_consent' });
        expect(src.reads).toBe(0);
        expect(people.upserts).toEqual([]);
    });

    test('consent to an older wording is not consent', async () => {
        const store = memoryStore({ [CONSENT_KEY]: JSON.stringify({ agreed: true, version: CONSENT_VERSION - 1, at: 'x' }) });
        expect(await hasConsent(store)).toBe(false);
    });

    test('without Contacts permission nothing is read', async () => {
        const store = memoryStore();
        await giveConsent(store);
        const src = source(contacts, 'denied');
        expect(await syncContacts({ source: src, people: recordingPeople(), store })).toEqual({ status: 'no_permission', permission: 'denied' });
        expect(src.reads).toBe(0);
    });

    test('incremental: the second sync sends only the change', async () => {
        const store = memoryStore();
        await giveConsent(store);
        const people = recordingPeople();
        const first = await syncContacts({ source: source(contacts), people, store, now: new Date('2026-10-07T10:00:00Z') });
        expect(first).toMatchObject({ status: 'synced', added: 2, changed: 0, removed: 0 });
        const second = await syncContacts({
            source: source([{ id: '1', name: 'Asha V', phones: ['9876543210'] }]),
            people,
            store,
        });
        expect(second).toMatchObject({ status: 'synced', added: 0, changed: 1, removed: 1, unchanged: 0 });
        expect(people.upserts).toEqual([['1', '2'], ['1']]);
        expect(people.removes).toEqual([['2']]);
        expect(await store.get(LAST_SYNC_KEY)).not.toBeNull();
    });

    test('a failed send keeps the old snapshot, so the next sync sends it again', async () => {
        const store = memoryStore();
        await giveConsent(store);
        const failing = { ...recordingPeople(), upsert: async () => Promise.reject(new Error('offline')) };
        const result = await syncContacts({ source: source(contacts), people: failing, store });
        expect(result).toEqual({ status: 'failed', message: 'offline' });
        expect(await store.get(SNAPSHOT_KEY)).toBeNull();
        const people = recordingPeople();
        await syncContacts({ source: source(contacts), people, store });
        expect(people.upserts).toEqual([['1', '2']]);
    });

    test('nothing changed: nothing is sent', async () => {
        const store = memoryStore();
        await giveConsent(store);
        await syncContacts({ source: source(contacts), people: recordingPeople(), store });
        const people = recordingPeople();
        const again = await syncContacts({ source: source(contacts), people, store });
        expect(again).toMatchObject({ added: 0, changed: 0, removed: 0, unchanged: 2 });
        expect(people.upserts).toEqual([]);
        expect(people.removes).toEqual([]);
    });

    test('stopping removes the synced records and withdraws consent', async () => {
        const store = memoryStore();
        await giveConsent(store);
        const people = new DevicePeopleApi(store);
        await syncContacts({ source: source(contacts), people, store });
        expect(await people.list()).toHaveLength(2);
        await stopSyncing({ people, store });
        expect(await people.list()).toEqual([]);
        expect(await hasConsent(store)).toBe(false);
        expect(await store.get(SNAPSHOT_KEY)).toBeNull();
    });
});

describe('the device People store (until the People API lands)', () => {
    test('lists, searches by name, number or email, and never invents a brief', async () => {
        const store = memoryStore();
        const people = new DevicePeopleApi(store);
        await people.upsert(normalizeAll([
            { id: '1', name: 'Asha Verma', phones: ['9876543210'], emails: ['asha@example.com'] },
            { id: '2', name: 'Rahul', phones: ['9123456789'] },
        ]));
        expect((await people.list()).map((p) => p.name)).toEqual(['Asha Verma', 'Rahul']);
        expect((await people.list('verma')).map((p) => p.id)).toEqual(['1']);
        expect((await people.list('91234')).map((p) => p.id)).toEqual(['2']);
        expect(await people.get('1')).toMatchObject({ brief: null, interactions: [] });
        expect(people.kind).toBe('device');
    });
});
