/**
 * Reading the public marketplace, from the server, for pages a stranger and
 * a search engine can both read.
 *
 * Server-side on purpose: a client-rendered shelf is an empty page to a
 * crawler, and the whole point of making it public is to be found. Cached
 * for five minutes — the shelf changes when a role is published, not per
 * request.
 */

import { getServerBackendUrl } from "@/lib/apiClient";

export type PublicFact = {
    key: string;
    question: string;
    kind: string;
    required: boolean;
    example?: string | null;
    used_for?: string | null;
};

export type PublicConnector = { app: string; label: string; used_for?: string | null; required: boolean };

export type PublicStep = {
    key: string;
    title: string;
    detail: string;
    blocking: boolean;
    facts?: PublicFact[];
    connectors?: PublicConnector[];
    demo_url?: string | null;
};

export type PublicCard = {
    slug: string;
    name: string;
    summary: string;
    job: string;
    publisher: { slug: string; name: string; first_party: boolean };
    badges: string[];
    industries: string[];
    languages: string[];
    demo_url: string | null;
};

export type PublicShelf = { jobs: string[]; packs: PublicCard[] };

export type PublicPack = {
    card: PublicCard;
    template_id: string;
    flow: string;
    steps: PublicStep[];
    guardrails: string[];
    compliance_notes: string[];
    outline: { name: string; kind: "start" | "step" | "finish" }[];
    speaks: boolean;
    runs: string | null;
};

const REVALIDATE_SECONDS = 300;

function base(): string {
    return `${getServerBackendUrl()}/api/v1/public/marketplace`;
}

export async function fetchShelf(params: { q?: string; job?: string }): Promise<PublicShelf | null> {
    const search = new URLSearchParams();
    if (params.q) search.set("q", params.q);
    if (params.job) search.set("job", params.job);
    const query = search.toString();
    try {
        const response = await fetch(`${base()}${query ? `?${query}` : ""}`, {
            next: { revalidate: REVALIDATE_SECONDS },
        });
        if (!response.ok) return null;
        return (await response.json()) as PublicShelf;
    } catch {
        return null;
    }
}

export async function fetchPack(slug: string): Promise<PublicPack | null> {
    try {
        const response = await fetch(`${base()}/${encodeURIComponent(slug)}`, {
            next: { revalidate: REVALIDATE_SECONDS },
        });
        if (!response.ok) return null;
        return (await response.json()) as PublicPack;
    } catch {
        return null;
    }
}

/** A step's place in the work, in the words of the role's channel. A role
 *  that does not ring a phone is never described as a call. */
export function stepKindLabel(kind: string, speaks: boolean): string {
    if (kind === "start") return speaks ? "Call opens" : "Starts";
    if (kind === "finish") return speaks ? "Call ends" : "Finishes";
    return "Step";
}

/** Everything the role will ask, across its hiring steps, in order. */
export function factsOf(pack: PublicPack): PublicFact[] {
    return pack.steps.flatMap((step) => step.facts ?? []);
}

/** Every app it can use, across its hiring steps, in order. */
export function connectorsOf(pack: PublicPack): PublicConnector[] {
    return pack.steps.flatMap((step) => step.connectors ?? []);
}
