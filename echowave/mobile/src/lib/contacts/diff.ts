/**
 * Contact sync, the pure part: turn the address book into person records and
 * say what changed since the last sync.
 *
 * A record is what would leave the phone (after consent): a name, phone
 * numbers in E.164 and email addresses in lower case, each sorted and once.
 * Its fingerprint is a hash of exactly that, so a contact whose number was
 * retyped in another shape ("098765 43210" -> "+91 98765 43210") is not a
 * change, and a contact edited in a field Decibyl never reads (a photo, a
 * birthday) is not sent again.
 */
import { toE164 } from '@/lib/phone';

export type DeviceContact = {
    id: string;
    name?: string | null;
    phones?: (string | null | undefined)[];
    emails?: (string | null | undefined)[];
};

export type PersonRecord = {
    /** The address book's own id for the contact, stable across syncs. */
    device_id: string;
    name: string;
    phones: string[];
    emails: string[];
    fingerprint: string;
};

/** device_id -> fingerprint, as of the last sync that succeeded. */
export type Snapshot = Record<string, string>;

export type ContactDiff = {
    added: PersonRecord[];
    changed: PersonRecord[];
    removed: string[];
    unchanged: number;
    /** The snapshot to keep once the server has the changes. */
    next: Snapshot;
};

const EMAIL = /^[^\s@]+@[^\s@]+\.[^\s@]+$/;

function unique(values: string[]): string[] {
    return [...new Set(values)].sort();
}

/** FNV-1a, 32 bit, as hex: stable, fast, and enough to notice a change. */
export function hash(text: string): string {
    let h = 0x811c9dc5;
    for (let i = 0; i < text.length; i += 1) {
        h ^= text.charCodeAt(i);
        h = Math.imul(h, 0x01000193) >>> 0;
    }
    return h.toString(16).padStart(8, '0');
}

export function normalizeContact(contact: DeviceContact, country = 'IN'): PersonRecord | null {
    if (!contact.id) return null;
    const phones = unique(
        (contact.phones ?? []).map((p) => toE164(p ?? '', country)).filter((p): p is string => Boolean(p)),
    );
    const emails = unique(
        (contact.emails ?? [])
            .map((e) => (e ?? '').trim().toLowerCase())
            .filter((e) => EMAIL.test(e)),
    );
    const name = (contact.name ?? '').replace(/\s+/g, ' ').trim();
    // Nothing to know a person by: not a person record.
    if (!phones.length && !emails.length) return null;
    const display = name || phones[0] || emails[0];
    const fingerprint = hash(JSON.stringify([display, phones, emails]));
    return { device_id: contact.id, name: display, phones, emails, fingerprint };
}

export function normalizeAll(contacts: DeviceContact[], country = 'IN'): PersonRecord[] {
    const out: PersonRecord[] = [];
    const seen = new Set<string>();
    for (const contact of contacts) {
        const record = normalizeContact(contact, country);
        if (!record || seen.has(record.device_id)) continue;
        seen.add(record.device_id);
        out.push(record);
    }
    return out;
}

export function diffContacts(previous: Snapshot, current: PersonRecord[]): ContactDiff {
    const added: PersonRecord[] = [];
    const changed: PersonRecord[] = [];
    const next: Snapshot = {};
    let unchanged = 0;
    for (const record of current) {
        next[record.device_id] = record.fingerprint;
        const before = previous[record.device_id];
        if (before === undefined) added.push(record);
        else if (before !== record.fingerprint) changed.push(record);
        else unchanged += 1;
    }
    const removed = Object.keys(previous).filter((id) => !(id in next));
    return { added, changed, removed, unchanged, next };
}
