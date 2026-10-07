"use client";

/**
 * One settings form on the person's own preferences (screens 17-19).
 *
 * The design's save contract: dirty -> saving -> confirmed saved. A rejection
 * keeps the draft and the last confirmed value and says why; a revision
 * conflict keeps both versions on screen ("Keep mine" / "Use saved") and
 * never overwrites silently. Only the fields this form owns are sent, so two
 * forms on two pages never undo each other. Leaving with unsaved changes is
 * guarded by the page's UnsavedChangesProvider.
 */

import { useCallback, useEffect, useMemo, useRef, useState } from "react";

import { myProfileApiV1MeSettingsProfileGet, saveMyProfileApiV1MeSettingsProfilePut } from "@/client/sdk.gen";
import type { Profile, ProfileWrite } from "@/client/types.gen";
import type { SaveState } from "@/components/shell/SaveBar";
import { useUnsavedChanges } from "@/context/UnsavedChangesContext";
import { detailFromError } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

export type ProfileField = Exclude<keyof ProfileWrite, "revision">;
export type Draft = Partial<Pick<Profile, ProfileField>>;

export type LoadPhase = "loading" | "failed" | "ready";

function pick(profile: Profile, fields: readonly ProfileField[]): Draft {
    const out: Draft = {};
    for (const field of fields) (out as Record<string, unknown>)[field] = profile[field] ?? null;
    return out;
}

function same(a: unknown, b: unknown): boolean {
    return (a ?? null) === (b ?? null);
}

export function useProfileForm(id: string, fields: readonly ProfileField[]) {
    const { user, loading: authLoading } = useAuth();
    const [phase, setPhase] = useState<LoadPhase>("loading");
    const [stored, setStored] = useState<Profile | null>(null);
    const [draft, setDraft] = useState<Draft>({});
    const [state, setState] = useState<SaveState>("clean");
    const [message, setMessage] = useState<string | null>(null);
    const [theirs, setTheirs] = useState<Profile | null>(null);
    const started = useRef(false);

    const load = useCallback(async () => {
        setPhase("loading");
        try {
            const result = await myProfileApiV1MeSettingsProfileGet();
            if (result.error || !result.data) {
                setMessage(detailFromError(result.error, "Could not load your settings."));
                setPhase("failed");
                return;
            }
            setStored(result.data);
            setDraft(pick(result.data, fields));
            setState("clean");
            setPhase("ready");
        } catch {
            setMessage("Could not reach Decibyl. Check your connection.");
            setPhase("failed");
        }
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    useEffect(() => {
        if (authLoading || !user || started.current) return;
        started.current = true;
        void load();
    }, [authLoading, user, load]);

    const changed = useMemo(() => {
        if (!stored) return [] as ProfileField[];
        return fields.filter((field) => !same(draft[field], stored[field]));
    }, [draft, stored, fields]);
    const dirty = changed.length > 0;
    useUnsavedChanges(id, dirty && state !== "saving");

    const set = useCallback(<K extends ProfileField>(field: K, value: Draft[K]) => {
        setDraft((was) => ({ ...was, [field]: value }));
        setState((was) => (was === "conflict" ? was : "dirty"));
        setMessage(null);
    }, []);

    // "saved" fades back to clean; "dirty" follows the draft.
    useEffect(() => {
        if (state === "saved") {
            const timer = setTimeout(() => setState("clean"), 2500);
            return () => clearTimeout(timer);
        }
        if (state === "dirty" && !dirty) setState("clean");
        if (state === "clean" && dirty) setState("dirty");
    }, [state, dirty]);

    const save = useCallback(async () => {
        if (!stored || changed.length === 0) return;
        setState("saving");
        setMessage(null);
        const body: ProfileWrite = { revision: stored.revision };
        for (const field of changed) (body as Record<string, unknown>)[field] = draft[field] ?? null;
        try {
            const result = await saveMyProfileApiV1MeSettingsProfilePut({ body });
            if (result.response?.status === 409) {
                const detail = (result.error as { detail?: { stored?: Profile; message?: string } })?.detail;
                setTheirs(detail?.stored ?? null);
                setMessage(detail?.message ?? null);
                setState("conflict");
                return;
            }
            if (result.error || !result.data) {
                // The draft and the last confirmed value both stay.
                setMessage(detailFromError(result.error, "Your change was not saved. Try again."));
                setState("rejected");
                return;
            }
            setStored(result.data);
            setDraft(pick(result.data, fields));
            setTheirs(null);
            setState("saved");
        } catch {
            setMessage("Your change was not saved. Check your connection and try again.");
            setState("rejected");
        }
    }, [stored, changed, draft, fields]);

    const discard = useCallback(() => {
        if (!stored) return;
        setDraft(pick(stored, fields));
        setTheirs(null);
        setMessage(null);
        setState("clean");
    }, [stored, fields]);

    /** After a conflict: save mine over what is stored now. */
    const keepMine = useCallback(() => {
        if (!theirs) return;
        setStored(theirs);
        setTheirs(null);
        setMessage(null);
        setState("dirty");
    }, [theirs]);

    /** After a conflict: take what is stored now. */
    const takeSaved = useCallback(() => {
        if (!theirs) return;
        setStored(theirs);
        setDraft(pick(theirs, fields));
        setTheirs(null);
        setMessage(null);
        setState("clean");
    }, [theirs, fields]);

    return {
        phase,
        stored,
        draft,
        set,
        dirty,
        changed,
        state,
        message,
        theirs,
        save,
        discard,
        keepMine,
        takeSaved,
        reload: load,
    };
}
