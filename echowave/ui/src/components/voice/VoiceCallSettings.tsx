"use client";

/**
 * Calls, inside Settings -> Voice and language (stream `voice`), shown while
 * the Call and Appointment helper or "call it for me" is switched on: what a
 * call may book, who may book, who a caller is handed to, the helper that
 * places calls, and the bookings made -- each with its honest setup state.
 * The rest of the screen (language, voice, preview, speed, captions,
 * microphone) is the settings stream's.
 */

import { useEffect, useState } from "react";

import {
    appointmentPolicyApiV1VoiceAppointmentsPolicyGet,
    getWorkflowsApiV1WorkflowFetchGet,
    saveAppointmentPolicyApiV1VoiceAppointmentsPolicyPut,
    upcomingAppointmentsApiV1VoiceAppointmentsGet,
    voiceReadinessApiV1VoiceReadinessGet,
} from "@/client/sdk.gen";
import type { Appointment, AppointmentPolicy, ReadinessState } from "@/client/types.gen";
import { ConnectionRow, SaveBar, type SaveState, SettingsSection } from "@/components/shell";
import { detailFromError } from "@/lib/apiError";
import { useFeature } from "@/lib/features";

const FIELD = "min-h-11 w-full rounded-[8px] border border-[#7B8491] bg-background px-3 text-[16px] md:text-sm";

/** Renders nothing unless one of the call switches is on. */
export function VoiceCallSettings() {
    const appointments = useFeature("call_appointment");
    const callForMe = useFeature("call_for_me");
    if (!appointments && !callForMe) return null;
    return <CallSettings appointments={appointments} />;
}

const BOOKING_LINE: Record<AppointmentPolicy["booking"], string> = {
    off: "Calls take details and the team calls back. Nothing is booked.",
    suggest: "Calls offer open times but never confirm one.",
    book: "Calls book an open time inside your hours, with the caller's details.",
};

function CallSettings({ appointments }: { appointments: boolean }) {
    const [readiness, setReadiness] = useState<ReadinessState | null>(null);
    const [policy, setPolicy] = useState<AppointmentPolicy | null>(null);
    const [draft, setDraft] = useState<AppointmentPolicy | null>(null);
    const [helpers, setHelpers] = useState<{ id: number; name: string }[]>([]);
    const [upcoming, setUpcoming] = useState<Appointment[] | null>(null);
    const [save, setSave] = useState<SaveState>("clean");
    const [message, setMessage] = useState<string | null>(null);

    useEffect(() => {
        void (async () => {
            const state = await voiceReadinessApiV1VoiceReadinessGet();
            setReadiness(state.data?.calls ?? null);
            if (!appointments) return;
            const [p, w, a] = await Promise.all([
                appointmentPolicyApiV1VoiceAppointmentsPolicyGet(),
                getWorkflowsApiV1WorkflowFetchGet(),
                upcomingAppointmentsApiV1VoiceAppointmentsGet(),
            ]);
            if (p.data) {
                setPolicy(p.data);
                setDraft(p.data);
            }
            setHelpers((w.data ?? []).map((wf) => ({ id: wf.id, name: wf.name })));
            setUpcoming(a.data ?? null);
        })();
    }, [appointments]);

    const change = (next: Partial<AppointmentPolicy>) => {
        setDraft((was) => (was ? { ...was, ...next } : was));
        setSave("dirty");
    };

    const submit = async () => {
        if (!draft || !policy) return;
        setSave("saving");
        const response = await saveAppointmentPolicyApiV1VoiceAppointmentsPolicyPut({
            body: {
                revision: policy.revision,
                booking: draft.booking,
                duration_minutes: draft.duration_minutes,
                services: draft.services,
                verification: draft.verification,
                escalate_to: draft.escalate_to || null,
                call_workflow_id: draft.call_workflow_id,
            },
        });
        if (response.error || !response.data) {
            const status = response.response?.status;
            setMessage(
                status === 403
                    ? "Only a workspace admin can change this."
                    : detailFromError(response.error, "Your change was not saved. Try again."),
            );
            setSave(status === 409 ? "conflict" : "rejected");
            return;
        }
        setPolicy(response.data);
        setDraft(response.data);
        setSave("saved");
    };

    return (
        <SettingsSection
            id="calls"
            title="Calls"
            scope="Workspace"
            description="What the Call and Appointment helper may do on calls, and who places calls you ask Decibyl to make."
        >
            <div className="flex flex-col gap-5 text-sm">
                {readiness && (
                    <ConnectionRow
                        name="Call it for me"
                        state={readiness.state}
                        reason={[readiness.reason, readiness.next_step].filter(Boolean).join(" ") || undefined}
                        detail={readiness.state === "available" ? "Every call is approved by you first and opens by saying it is Decibyl calling for you." : undefined}
                    />
                )}
                {appointments && draft && (
                    <>
                        <fieldset className="flex flex-col gap-2">
                            <legend className="mb-1 font-medium">Booking on calls</legend>
                            {(["off", "suggest", "book"] as const).map((mode) => (
                                <label key={mode} className="flex min-h-11 items-start gap-2">
                                    <input type="radio" name="booking" className="mt-1" checked={draft.booking === mode} onChange={() => change({ booking: mode })} />
                                    <span>
                                        <span className="block font-medium">{mode === "off" ? "Off" : mode === "suggest" ? "Offer times only" : "Book within hours"}</span>
                                        <span className="text-muted-foreground">{BOOKING_LINE[mode]}</span>
                                    </span>
                                </label>
                            ))}
                        </fieldset>
                        <label className="flex flex-col gap-1.5">
                            <span className="font-medium">Appointment length (minutes)</span>
                            <input type="number" min={10} max={240} step={5} className={FIELD} value={draft.duration_minutes} onChange={(e) => change({ duration_minutes: Number(e.target.value) })} />
                        </label>
                        <label className="flex flex-col gap-1.5">
                            <span className="font-medium">Services</span>
                            <input
                                className={FIELD}
                                value={draft.services.join(", ")}
                                onChange={(e) => change({ services: e.target.value.split(",").map((s) => s.trim()).filter(Boolean) })}
                                placeholder="Cleaning, Check-up"
                            />
                            <span className="text-muted-foreground">Separated by commas. Leave empty to accept any reason.</span>
                        </label>
                        <label className="flex flex-col gap-1.5">
                            <span className="font-medium">Who may book</span>
                            <select className={FIELD} value={draft.verification} onChange={(e) => change({ verification: e.target.value as AppointmentPolicy["verification"] })}>
                                <option value="details">Anyone who gives their name, number and reason</option>
                                <option value="known_caller">Only callers already in your contacts</option>
                            </select>
                            <span className="text-muted-foreground">Calls never read out an existing booking, whoever is calling.</span>
                        </label>
                        <label className="flex flex-col gap-1.5">
                            <span className="font-medium">Hand callers to</span>
                            <input className={FIELD} inputMode="tel" value={draft.escalate_to ?? ""} onChange={(e) => change({ escalate_to: e.target.value })} placeholder="+91 98765 43210" />
                            <span className="text-muted-foreground">When the helper is unsure. Empty: the team is told and calls back.</span>
                        </label>
                        <label className="flex flex-col gap-1.5">
                            <span className="font-medium">Helper that makes calls</span>
                            <select className={FIELD} value={draft.call_workflow_id ?? ""} onChange={(e) => change({ call_workflow_id: e.target.value ? Number(e.target.value) : null })}>
                                <option value="">None chosen</option>
                                {helpers.map((h) => (
                                    <option key={h.id} value={h.id}>
                                        {h.name}
                                    </option>
                                ))}
                            </select>
                            <span className="text-muted-foreground">Places &ldquo;call it for me&rdquo; calls and answers booking calls.</span>
                        </label>
                        <SaveBar state={save} onSave={() => void submit()} onDiscard={() => { setDraft(policy); setSave("clean"); }} message={message} className="-mx-6" />
                        <div>
                            <h3 className="mb-2 font-medium">Upcoming appointments</h3>
                            {upcoming === null ? (
                                <p className="text-muted-foreground">Could not load appointments.</p>
                            ) : upcoming.length === 0 ? (
                                <p className="text-muted-foreground">Nothing booked yet.</p>
                            ) : (
                                <ul className="divide-y divide-border">
                                    {upcoming.map((a) => (
                                        <li key={a.id} className="flex flex-wrap justify-between gap-2 py-2">
                                            <span>{new Date(a.starts_at).toLocaleString()}</span>
                                            <span className="text-muted-foreground">{[a.caller_name, a.service ?? a.reason].filter(Boolean).join(" · ")}</span>
                                        </li>
                                    ))}
                                </ul>
                            )}
                        </div>
                    </>
                )}
            </div>
        </SettingsSection>
    );
}
