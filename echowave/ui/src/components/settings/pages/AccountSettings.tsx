"use client";

/**
 * Settings -> Personal -> Account (screen 17; handoff 24 "Account").
 *
 * The person's name and confirmed timezone (their own -- never the team's),
 * the email they sign in with, how the app looks on this device, today's
 * allowances as the server counts them (no pricing grid), and sign out.
 * Saved with the Settings shell's save contract.
 */

import { CheckCircle2, LogOut } from "lucide-react";
import { useEffect, useMemo, useState } from "react";

import { myQuotasApiV1MeQuotasGet } from "@/client/sdk.gen";
import type { AllowanceRow } from "@/client/types.gen";
import { PageBody, PageHeader } from "@/components/layout/PageHeader";
import { MfaSection } from "@/components/MfaSection";
import { SettingsSection } from "@/components/shell/SettingsSection";
import { ThemeModeSection } from "@/components/ThemeModeSection";
import { Button } from "@/components/ui/button";
import { UnsavedChangesProvider } from "@/context/UnsavedChangesContext";
import { useAuth } from "@/lib/auth";
import { useFeature } from "@/lib/features";

import { Field, INPUT, SettingsFormFrame } from "../SettingsForm";
import { type ProfileField, useProfileForm } from "../useProfileForm";

const FIELDS: readonly ProfileField[] = ["preferred_name", "timezone"];
const LABELS: Partial<Record<ProfileField, string>> = { preferred_name: "Name", timezone: "Timezone" };

function zones(): string[] {
    try {
        const all = (Intl as unknown as { supportedValuesOf?: (key: string) => string[] }).supportedValuesOf?.("timeZone");
        if (all && all.length) return all;
    } catch {
        /* older browsers */
    }
    return ["Asia/Kolkata", "Asia/Dubai", "Asia/Singapore", "Europe/London", "America/New_York", "UTC"];
}

function detectedZone(): string | null {
    try {
        return Intl.DateTimeFormat().resolvedOptions().timeZone || null;
    } catch {
        return null;
    }
}

function Allowances() {
    const on = useFeature("operational_quotas");
    const { user, loading } = useAuth();
    const [rows, setRows] = useState<AllowanceRow[] | null>(null);
    const [failed, setFailed] = useState(false);
    const signedIn = !loading && Boolean(user);
    useEffect(() => {
        if (!on || !signedIn) return;
        void (async () => {
            const result = await myQuotasApiV1MeQuotasGet();
            if (result.error || !result.data) {
                setFailed(true);
                return;
            }
            setRows(result.data.allowances);
        })();
    }, [on, signedIn]);
    if (!on) return null;
    return (
        <SettingsSection id="allowances" title="Today's allowances" description="What is left today, as Decibyl counts it. Help can raise one." scope="Just you">
            {failed && <p className="text-sm text-muted-foreground">Could not load your allowances. They still apply.</p>}
            {rows && (
                <ul className="divide-y divide-border text-sm">
                    {rows.map((row) => (
                        <li key={row.kind} className="flex flex-wrap items-baseline justify-between gap-2 py-2">
                            <span>{row.kind.replace(/_/g, " ").replace(/^./, (c) => c.toUpperCase())}</span>
                            <span className="text-muted-foreground">
                                {row.remaining} of {row.limit} {row.unit} left · resets{" "}
                                {new Date(row.resets_at).toLocaleTimeString(undefined, { hour: "numeric", minute: "2-digit" })}
                            </span>
                        </li>
                    ))}
                </ul>
            )}
        </SettingsSection>
    );
}

function AccountForm() {
    const form = useProfileForm("settings-account", FIELDS);
    const { logout } = useAuth();
    const privacyCenter = useFeature("privacy_center");
    const detected = useMemo(detectedZone, []);
    const timezone = (form.draft.timezone as string | null) ?? "";
    const saved = (form.stored?.timezone as string | null) ?? "";
    // Browsers name some zones differently (Asia/Calcutta for Asia/Kolkata):
    // the stored and detected zones are always offered, never shown as unset.
    const options = useMemo(() => {
        const list = zones();
        for (const zone of [detected, saved, timezone]) if (zone && !list.includes(zone)) list.unshift(zone);
        return list;
    }, [detected, saved, timezone]);

    return (
        <SettingsFormFrame form={form} labels={LABELS}>
            <SettingsSection id="profile" title="You" description="How Decibyl addresses you, and the address you sign in with." scope="Just you">
                <div className="flex flex-col gap-5">
                    <Field id="name" label="Name" help="What Decibyl calls you. Your colleagues see the name on your sign-in.">
                        <input
                            id="name-input"
                            className={INPUT}
                            value={(form.draft.preferred_name as string | null) ?? ""}
                            maxLength={80}
                            autoComplete="nickname"
                            onChange={(event) => form.set("preferred_name", event.target.value)}
                        />
                    </Field>
                    <Field id="email" label="Sign-in email" help="Changing it needs a check of your identity; ask Help for now.">
                        <p className="flex min-h-11 flex-wrap items-center gap-2 break-all text-sm md:min-h-9" id="email-input">
                            {form.stored?.email ?? "No email on this account"}
                            {form.stored?.email && (
                                <span className="inline-flex items-center gap-1 text-xs text-muted-foreground">
                                    {form.stored.email_verified ? (
                                        <>
                                            <CheckCircle2 aria-hidden className="h-3.5 w-3.5 text-[#075A39]" /> Verified
                                        </>
                                    ) : (
                                        "Not verified yet"
                                    )}
                                </span>
                            )}
                        </p>
                    </Field>
                </div>
            </SettingsSection>

            <SettingsSection id="timezone" title="Timezone" description="Your reminders and your daily brief follow it. The team's schedules keep the workspace's timezone." scope="Just you">
                <Field id="timezone-field" label="Your timezone" help={detected && detected !== timezone ? `This device says ${detected}.` : undefined}>
                    <div className="flex flex-col gap-2 sm:flex-row">
                        <select
                            id="timezone-field-input"
                            className={INPUT}
                            value={timezone}
                            onChange={(event) => form.set("timezone", event.target.value || null)}
                        >
                            <option value="">Not set</option>
                            {options.map((zone) => (
                                <option key={zone} value={zone}>
                                    {zone.replace(/_/g, " ")}
                                </option>
                            ))}
                        </select>
                        {detected && detected !== timezone && (
                            <Button type="button" variant="outline" className="motion-m1 min-h-11 shrink-0 md:min-h-9" onClick={() => form.set("timezone", detected)}>
                                Use {detected.split("/").pop()?.replace(/_/g, " ")}
                            </Button>
                        )}
                    </div>
                </Field>
            </SettingsSection>

            <SettingsSection id="appearance" title="Appearance" description="Light, dark, or whatever this device is set to. Saved on this device." scope="This device">
                <ThemeModeSection />
            </SettingsSection>

            <Allowances />

            {!privacyCenter && (
                <SettingsSection id="security" title="Two-step sign-in" description="A code from an authenticator app at sign-in, on top of your password." scope="Just you">
                    <MfaSection />
                </SettingsSection>
            )}

            <SettingsSection id="devices" title="Signed-in devices" description="Where you are signed in." scope="Just you">
                {/* Honest: the list does not exist yet, and pretending would be worse. */}
                <p className="text-sm text-muted-foreground" data-testid="devices-unavailable">
                    Unavailable: the list of devices is not built yet. Signing out here signs this device out.
                </p>
            </SettingsSection>

            <div>
                <Button type="button" variant="outline" className="motion-m1 min-h-11 md:min-h-9" onClick={() => void logout()}>
                    <LogOut aria-hidden /> Sign out
                </Button>
            </div>
        </SettingsFormFrame>
    );
}

export function AccountSettings() {
    return (
        <UnsavedChangesProvider>
            <PageHeader title="Account" description="Yours alone: nothing here changes anything for your colleagues." />
            <PageBody className="max-w-[640px]">
                <AccountForm />
            </PageBody>
        </UnsavedChangesProvider>
    );
}

export default AccountSettings;
