"use client";

/**
 * Simple mode (launch stream `care`): large text, voice first, one thing at
 * a time, fewer choices.
 *
 * A person's own preference, saved with their other preferences
 * (`member_preferences.simple_mode`, PUT /me/preferences) so it follows them
 * to every device. Offered only while `care_simple_mode` and
 * `member_preferences` are both on; while either is off this provider reads
 * nothing, writes nothing and the app is exactly as before.
 *
 * On, it sets `data-simple-mode="on"` on <html>; `simple-mode.css` scales
 * the text and the targets from that one attribute, and the profile menu and
 * the Care hub read `useSimpleMode()` to offer fewer choices. The last known
 * value is kept on this device so the page does not flash small text before
 * the preference arrives; the server's answer always wins.
 */

import { createContext, type ReactNode, useCallback, useContext, useEffect, useMemo, useState } from "react";

import { myPreferencesApiV1MePreferencesGet, saveMyPreferencesApiV1MePreferencesPut } from "@/client/sdk.gen";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";
import { useFeature } from "@/lib/features";

const STORED = "decibyl:simple-mode";

export type SimpleModeState = {
    /** Whether Simple mode can be chosen here at all. */
    offered: boolean;
    on: boolean;
    saving: boolean;
    error: string | null;
    setOn: (next: boolean) => Promise<boolean>;
};

const OFF: SimpleModeState = {
    offered: false,
    on: false,
    saving: false,
    error: null,
    setOn: async () => false,
};

const SimpleModeContext = createContext<SimpleModeState>(OFF);

function readStored(): boolean {
    try {
        return window.localStorage.getItem(STORED) === "on";
    } catch {
        return false;
    }
}

function writeStored(on: boolean) {
    try {
        if (on) window.localStorage.setItem(STORED, "on");
        else window.localStorage.removeItem(STORED);
    } catch {
        // No storage: the attribute still follows the server's answer.
    }
}

export function applySimpleMode(on: boolean) {
    if (typeof document === "undefined") return;
    if (on) document.documentElement.setAttribute("data-simple-mode", "on");
    else document.documentElement.removeAttribute("data-simple-mode");
}

export function SimpleModeProvider({ children }: { children: ReactNode }) {
    const flag = useFeature("care_simple_mode");
    const prefs = useFeature("member_preferences");
    const offered = flag && prefs;
    const [state, setState] = useState<SimpleModeState>(OFF);

    useEffect(() => {
        if (!offered) applySimpleMode(false);
    }, [offered]);

    // The part that reads auth and the preference is mounted only while
    // Simple mode is offered, so a switched-off app does not touch either.
    return (
        <SimpleModeContext.Provider value={offered ? state : OFF}>
            {offered && <SimpleModeLoader onState={setState} />}
            {children}
        </SimpleModeContext.Provider>
    );
}

function SimpleModeLoader({ onState }: { onState: (state: SimpleModeState) => void }) {
    const { user, loading } = useAuth();
    // A boolean, not the user object: re-read only when the person signs in
    // or out, never on a re-render.
    const signedIn = Boolean(user);
    const [on, setOnState] = useState(false);
    const [revision, setRevision] = useState<number | null>(null);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // The device's last answer first, so large text does not flash small.
    useEffect(() => {
        const stored = readStored();
        setOnState(stored);
        applySimpleMode(stored);
    }, []);

    useEffect(() => {
        if (loading || !signedIn) return;
        let cancelled = false;
        void (async () => {
            const response = await myPreferencesApiV1MePreferencesGet();
            if (cancelled || response.error || !response.data) return;
            const value = Boolean(response.data.simple_mode);
            setRevision(response.data.revision);
            setOnState(value);
            writeStored(value);
            applySimpleMode(value);
        })();
        return () => {
            cancelled = true;
        };
    }, [loading, signedIn]);

    const setOn = useCallback(
        async (next: boolean) => {
            setSaving(true);
            setError(null);
            let current = revision;
            if (current === null) {
                const read = await myPreferencesApiV1MePreferencesGet();
                current = read.data?.revision ?? 0;
            }
            let response = await saveMyPreferencesApiV1MePreferencesPut({
                body: { simple_mode: next, revision: current },
            });
            if (response.response?.status === 409) {
                // Changed in another tab: this one switch is all that is
                // being saved, so save it on top of what is stored now.
                const read = await myPreferencesApiV1MePreferencesGet();
                response = await saveMyPreferencesApiV1MePreferencesPut({
                    body: { simple_mode: next, revision: read.data?.revision ?? 0 },
                });
            }
            setSaving(false);
            if (response.error || !response.data) {
                setError(detailFromResult(response, "Simple mode was not saved. Try again."));
                return false;
            }
            const value = Boolean(response.data.simple_mode);
            setRevision(response.data.revision);
            setOnState(value);
            writeStored(value);
            applySimpleMode(value);
            return true;
        },
        [revision],
    );

    const value = useMemo<SimpleModeState>(
        () => ({ offered: true, on, saving, error, setOn }),
        [on, saving, error, setOn],
    );
    useEffect(() => {
        onState(value);
    }, [value, onState]);
    useEffect(() => () => onState(OFF), [onState]);
    return null;
}

export function useSimpleMode(): SimpleModeState {
    return useContext(SimpleModeContext);
}
