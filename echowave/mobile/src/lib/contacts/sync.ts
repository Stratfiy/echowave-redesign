/**
 * One contact sync: read the address book, diff it against the last sync,
 * send only what changed. Runs on app open and in the background
 * (src/lib/background/tasks.ts), and from "Sync now".
 *
 * Order matters for honesty:
 * 1. No consent: nothing is read, nothing is sent.
 * 2. No permission: nothing is read; the screen asks for it.
 * 3. The snapshot is saved only after the server took the changes, so a sync
 *    that failed half-way sends the rest next time instead of forgetting it.
 */
import type { PeopleApi } from '@/lib/people/api';
import type { KeyValue } from '@/lib/storage';

import { hasConsent, withdrawConsent } from './consent';
import { diffContacts, normalizeAll, type DeviceContact, type Snapshot } from './diff';

export const SNAPSHOT_KEY = 'decibyl.people.snapshot';
export const LAST_SYNC_KEY = 'decibyl.people.lastSync';
export const BATCH = 200;

export type Permission = 'granted' | 'denied' | 'undetermined';

export interface ContactSource {
    available(): boolean;
    permission(): Promise<Permission>;
    request(): Promise<Permission>;
    read(): Promise<DeviceContact[]>;
}

export type SyncResult =
    | { status: 'no_consent' }
    | { status: 'unavailable' }
    | { status: 'no_permission'; permission: Permission }
    | { status: 'synced'; added: number; changed: number; removed: number; unchanged: number; at: string }
    | { status: 'failed'; message: string };

async function readSnapshot(store: KeyValue): Promise<Snapshot> {
    const raw = await store.get(SNAPSHOT_KEY);
    if (!raw) return {};
    try {
        return JSON.parse(raw) as Snapshot;
    } catch {
        return {};
    }
}

export async function lastSyncAt(store: KeyValue): Promise<string | null> {
    return store.get(LAST_SYNC_KEY);
}

export async function syncContacts(options: {
    source: ContactSource;
    people: PeopleApi;
    store: KeyValue;
    country?: string;
    now?: Date;
}): Promise<SyncResult> {
    const { source, people, store } = options;
    if (!(await hasConsent(store))) return { status: 'no_consent' };
    if (!source.available()) return { status: 'unavailable' };
    const permission = await source.permission();
    if (permission !== 'granted') return { status: 'no_permission', permission };
    try {
        const records = normalizeAll(await source.read(), options.country ?? 'IN');
        const diff = diffContacts(await readSnapshot(store), records);
        const outgoing = [...diff.added, ...diff.changed];
        for (let i = 0; i < outgoing.length; i += BATCH) {
            await people.upsert(outgoing.slice(i, i + BATCH));
        }
        if (diff.removed.length) await people.remove(diff.removed);
        const at = (options.now ?? new Date()).toISOString();
        await store.set(SNAPSHOT_KEY, JSON.stringify(diff.next));
        await store.set(LAST_SYNC_KEY, at);
        return {
            status: 'synced',
            added: diff.added.length,
            changed: diff.changed.length,
            removed: diff.removed.length,
            unchanged: diff.unchanged,
            at,
        };
    } catch (error) {
        return { status: 'failed', message: error instanceof Error ? error.message : 'Sync failed' };
    }
}

/** Withdraw: remove what was synced, forget the snapshot, stop syncing. */
export async function stopSyncing(options: { people: PeopleApi; store: KeyValue }): Promise<void> {
    await options.people.removeAll();
    await options.store.remove(SNAPSHOT_KEY);
    await options.store.remove(LAST_SYNC_KEY);
    await withdrawConsent(options.store);
}
