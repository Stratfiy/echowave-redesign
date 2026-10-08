/** What was shared into the app, as the items the share screen shows. */
import type * as Sharing from 'expo-sharing';

export type Item = { kind: 'link' | 'text' | 'file'; value: string; name?: string; mime?: string | null; size?: number | null };

export function itemsFrom(payloads: Sharing.ResolvedSharePayload[] | Sharing.SharePayload[], params: { text?: string; url?: string }): Item[] {
    const items: Item[] = [];
    for (const p of payloads as (Sharing.SharePayload & Partial<Sharing.ResolvedSharePayload>)[]) {
        const type = p.shareType ?? 'text';
        if (type === 'url' || p.contentType === 'website') items.push({ kind: 'link', value: p.value ?? p.contentUri ?? '' });
        else if (type === 'text') items.push({ kind: 'text', value: p.value ?? '' });
        else items.push({ kind: 'file', value: p.contentUri ?? p.value ?? '', name: p.originalName ?? 'shared-file', mime: p.contentMimeType ?? p.mimeType, size: p.contentSize ?? null });
    }
    if (params.url) items.push({ kind: 'link', value: params.url });
    if (params.text) items.push({ kind: 'text', value: params.text });
    return items.filter((i) => i.value);
}

