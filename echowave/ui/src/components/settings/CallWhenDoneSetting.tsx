'use client';

import { useEffect, useRef, useState } from 'react';

import { myPreferencesApiV1MePreferencesGet, saveMyPreferencesApiV1MePreferencesPut } from '@/client/sdk.gen';
import { Switch } from '@/components/ui/switch';
import { detailFromError } from '@/lib/apiError';
import { useAuth } from '@/lib/auth';
import { useFeature } from '@/lib/features';

/**
 * "Call me when long tasks finish": the standing preference on the person's
 * own row (member_preferences). Shown only while `call_when_done` is on;
 * saved on toggle against the revision it read.
 */
export function CallWhenDoneSetting() {
    const on = useFeature('call_when_done');
    const prefsOn = useFeature('member_preferences');
    const { user, loading: authLoading } = useAuth();
    const [value, setValue] = useState<boolean | null>(null);
    const [revision, setRevision] = useState(0);
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const fetched = useRef(false);

    useEffect(() => {
        if (!on || !prefsOn || authLoading || !user || fetched.current) return;
        fetched.current = true;
        void (async () => {
            const response = await myPreferencesApiV1MePreferencesGet();
            if (response.error) {
                setError(detailFromError(response.error, 'Could not load this setting'));
                return;
            }
            setValue(Boolean(response.data?.call_when_done));
            setRevision(response.data?.revision ?? 0);
        })();
    }, [on, prefsOn, authLoading, user]);

    if (!on || !prefsOn) return null;

    const change = async (next: boolean) => {
        setSaving(true);
        setError(null);
        const response = await saveMyPreferencesApiV1MePreferencesPut({
            body: { call_when_done: next, revision },
        });
        setSaving(false);
        if (response.error) {
            setError(detailFromError(response.error, 'Could not save this setting'));
            return;
        }
        setValue(Boolean(response.data?.call_when_done));
        setRevision(response.data?.revision ?? revision);
    };

    return (
        <div className="mb-6 rounded-[var(--radius-card,0.75rem)] border border-border p-3" data-testid="call-when-done-setting">
            <div className="flex min-h-11 items-center justify-between gap-3">
                <label htmlFor="call-when-done" className="min-w-0 flex-1 cursor-pointer text-sm">
                    <span className="block">Call me when long tasks finish</span>
                    <span className="block text-xs text-muted-foreground">
                        Decibyl rings you between 9:00 and 21:00 with the result. Where it cannot call, you are told here and
                        by notification.
                    </span>
                </label>
                <Switch
                    id="call-when-done"
                    checked={Boolean(value)}
                    disabled={value === null || saving}
                    onCheckedChange={(next) => void change(next)}
                    aria-label="Call me when long tasks finish"
                />
            </div>
            {error && (
                <p role="alert" className="mt-1 text-xs text-destructive">
                    {error}
                </p>
            )}
        </div>
    );
}
