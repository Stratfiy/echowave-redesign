/**
 * People: the person's synced contacts, each with a short brief and their
 * last interactions with Decibyl.
 *
 * The server side is being built by the People stream (branch
 * `claude/people`, flag `people`) and has not landed, so its routes are not
 * in ui/openapi.internal.json yet and there is no generated client to call.
 * The app is written against this interface instead; until the server
 * lands it runs on `DevicePeopleApi`, which keeps the records on the phone
 * only (nothing is sent anywhere), and the People screen says so.
 *
 * When the People routes land: regenerate the client (`npm run
 * generate-client`), implement `PeopleApi` with the generated `people*`
 * functions in `serverPeopleApi.ts`, return it from `createPeopleApi`
 * while `features.people` is on, and bump `CONSENT_VERSION`
 * (src/lib/contacts/consent.ts): today's yes was to keeping contacts on the
 * phone, so the person is asked again before anything is sent. Screens and
 * sync do not change.
 */
import type { PersonRecord } from '@/lib/contacts/diff';
import type { KeyValue } from '@/lib/storage';

export type Interaction = {
    at: string;
    /** "message", "call", "email", "meeting"... as the server names it. */
    kind: string;
    summary: string;
    thread_id?: string | null;
};

export type Person = {
    id: string;
    name: string;
    phones: string[];
    emails: string[];
    last_interaction_at?: string | null;
};

export type PersonDetail = Person & {
    brief: string | null;
    interactions: Interaction[];
};

export interface PeopleApi {
    /** `server`: records go to the workspace; `device`: they stay here. */
    readonly kind: 'server' | 'device';
    upsert(records: PersonRecord[]): Promise<void>;
    remove(deviceIds: string[]): Promise<void>;
    /** Withdrawing consent: every synced record goes. */
    removeAll(): Promise<void>;
    list(search?: string): Promise<Person[]>;
    get(id: string): Promise<PersonDetail | null>;
}

const LOCAL_KEY = 'decibyl.people.local';

/** Keeps records on the phone only. Briefs and interactions need the server,
 * so they are empty here -- said on screen, never invented. */
export class DevicePeopleApi implements PeopleApi {
    readonly kind = 'device' as const;

    constructor(private readonly store: KeyValue) {}

    private async read(): Promise<Record<string, PersonRecord>> {
        const raw = await this.store.get(LOCAL_KEY);
        if (!raw) return {};
        try {
            return JSON.parse(raw) as Record<string, PersonRecord>;
        } catch {
            return {};
        }
    }

    private async write(records: Record<string, PersonRecord>): Promise<void> {
        await this.store.set(LOCAL_KEY, JSON.stringify(records));
    }

    async upsert(records: PersonRecord[]): Promise<void> {
        const held = await this.read();
        for (const record of records) held[record.device_id] = record;
        await this.write(held);
    }

    async remove(deviceIds: string[]): Promise<void> {
        const held = await this.read();
        for (const id of deviceIds) delete held[id];
        await this.write(held);
    }

    async removeAll(): Promise<void> {
        await this.store.remove(LOCAL_KEY);
    }

    async list(search?: string): Promise<Person[]> {
        const needle = (search ?? '').trim().toLowerCase();
        return Object.values(await this.read())
            .filter(
                (r) =>
                    !needle ||
                    r.name.toLowerCase().includes(needle) ||
                    r.phones.some((p) => p.includes(needle.replace(/\s/g, ''))) ||
                    r.emails.some((e) => e.includes(needle)),
            )
            .sort((a, b) => a.name.localeCompare(b.name))
            .map((r) => ({ id: r.device_id, name: r.name, phones: r.phones, emails: r.emails, last_interaction_at: null }));
    }

    async get(id: string): Promise<PersonDetail | null> {
        const record = (await this.read())[id];
        if (!record) return null;
        return {
            id: record.device_id,
            name: record.name,
            phones: record.phones,
            emails: record.emails,
            last_interaction_at: null,
            brief: null,
            interactions: [],
        };
    }
}

export function createPeopleApi(store: KeyValue): PeopleApi {
    // The server adapter goes here once the People routes are generated
    // (see the module docstring). Until then everything stays on the phone.
    return new DevicePeopleApi(store);
}
