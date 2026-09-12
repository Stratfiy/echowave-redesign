"use client";

/**
 * Connect Google Calendar where the card is.
 *
 * It used to be a button that sent people to the Provider keys screen, which
 * is the wrong room: there is no key to paste, only a Google sign-in. The
 * control now lives on the card itself — status, connect, disconnect — and
 * the OAuth round trip lands back on the Apps page, where this reads the
 * outcome from the query string and says what happened.
 *
 * Plain fetch rather than the generated client: the routes are newer than
 * the last client generation, and the bearer token is attached by hand.
 */

import { AlertTriangle, CalendarClock, CheckCircle2, Loader2 } from "lucide-react";
import { useRouter } from "next/navigation";
import { useCallback, useEffect, useRef, useState } from "react";

import { Button } from "@/components/ui/button";
import { useAuth } from "@/lib/auth";
import { cn } from "@/lib/utils";

type Status = {
    connected: boolean;
    connected_email: string | null;
    calendar_id: string | null;
    busy_calendar_ids?: string[] | null;
    configured: boolean;
};

async function calendarFetch(
    path: string,
    accessToken: string,
    init?: RequestInit,
): Promise<{ data?: unknown; error?: string }> {
    try {
        const response = await fetch(`/api/v1/integrations/google-calendar${path}`, {
            ...init,
            headers: { Authorization: `Bearer ${accessToken}`, ...(init?.headers ?? {}) },
        });
        const body = await response.json().catch(() => null);
        if (!response.ok) return { error: (body as { detail?: string })?.detail || "Request failed" };
        return { data: body };
    } catch {
        return { error: "Network error" };
    }
}

export function GoogleCalendarConnect({ returnPath = "/integrations/apps" }: { returnPath?: string }) {
    const auth = useAuth();
    const router = useRouter();
    const hasFetched = useRef(false);
    const [status, setStatus] = useState<Status | null>(null);
    const [loading, setLoading] = useState(true);
    const [busy, setBusy] = useState(false);
    const [message, setMessage] = useState<{ kind: "success" | "error"; text: string } | null>(null);

    const load = useCallback(async () => {
        const token = await auth.getAccessToken();
        const result = await calendarFetch("/status", token);
        if (!result.error && result.data) setStatus(result.data as Status);
        setLoading(false);
    }, [auth]);

    useEffect(() => {
        if (auth.loading || !auth.user || hasFetched.current) return;
        hasFetched.current = true;
        void load();
    }, [auth.loading, auth.user, load]);

    // Back from Google's consent screen: ?google_calendar=connected|error.
    useEffect(() => {
        const params = new URLSearchParams(window.location.search);
        const outcome = params.get("google_calendar");
        if (!outcome) return;
        if (outcome === "connected") {
            setMessage({ kind: "success", text: "Google Calendar connected." });
        } else if (outcome === "error") {
            setMessage({ kind: "error", text: `Could not connect: ${params.get("reason") || "something went wrong."}` });
        }
        router.replace(returnPath);
        // eslint-disable-next-line react-hooks/exhaustive-deps
    }, []);

    const connect = async () => {
        setBusy(true);
        setMessage(null);
        const token = await auth.getAccessToken();
        const result = await calendarFetch("/authorize-url", token);
        if (result.error || !result.data) {
            setMessage({ kind: "error", text: result.error || "Could not start the connect flow." });
            setBusy(false);
            return;
        }
        window.location.href = (result.data as { url: string }).url;
    };

    const disconnect = async () => {
        setBusy(true);
        setMessage(null);
        const token = await auth.getAccessToken();
        const result = await calendarFetch("/disconnect", token, { method: "POST" });
        if (result.error) setMessage({ kind: "error", text: result.error });
        else {
            setMessage({ kind: "success", text: "Google Calendar disconnected." });
            await load();
        }
        setBusy(false);
    };

    return (
        <div className="space-y-2">
            {loading ? (
                <p className="flex items-center gap-1.5 text-xs text-muted-foreground">
                    <Loader2 className="h-3 w-3 animate-spin" />
                    Checking…
                </p>
            ) : status?.connected ? (
                <p className="flex items-start gap-1.5 text-xs text-muted-foreground">
                    <CalendarClock className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    {/* The email is often absent, and "Connected as ." with a
                        blank is worse than not naming the account at all — it
                        reads as data we lost rather than data we never had.
                        The OAuth scope is calendar.events only, and the
                        userinfo endpoint needs an email scope to answer, so a
                        connection made before that scope is granted has no
                        email to show and never will. */}
                    <span>
                        {status.connected_email ? (
                            <>
                                Connected as{" "}
                                <span className="text-foreground">{status.connected_email}</span>.{" "}
                            </>
                        ) : (
                            <>Connected. </>
                        )}
                        Events go on the{" "}
                        {status.calendar_id === "primary" || !status.calendar_id ? "primary" : status.calendar_id} calendar.
                    </span>
                </p>
            ) : status && !status.configured ? (
                <p className="flex items-start gap-1.5 text-xs text-muted-foreground">
                    <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
                    Not available on this deployment yet: Google sign-in is not configured.
                </p>
            ) : (
                <p className="text-xs text-muted-foreground">Sign in with Google. No API key.</p>
            )}
            {message && (
                <p
                    className={cn(
                        "flex items-start gap-1.5 text-xs",
                        message.kind === "success" ? "text-emerald-700" : "text-destructive",
                    )}
                    role="status"
                >
                    {message.kind === "success" && <CheckCircle2 className="mt-0.5 h-3.5 w-3.5 shrink-0" />}
                    {message.text}
                </p>
            )}
            {status?.connected ? <BusyCalendars status={status} onSaved={load} /> : null}
            {status?.connected ? (
                <Button variant="outline" size="sm" className="w-full" onClick={() => void disconnect()} disabled={busy}>
                    {busy && <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />}
                    Disconnect
                </Button>
            ) : (
                <Button
                    variant="secondary"
                    size="sm"
                    className="w-full"
                    onClick={() => void connect()}
                    disabled={busy || loading || (status !== null && !status.configured)}
                >
                    {busy && <Loader2 className="mr-1.5 h-4 w-4 animate-spin" />}
                    Connect Google Calendar
                </Button>
            )}
        </div>
    );
}

/**
 * Calendars that are *read* when checking whether a slot is free.
 *
 * An appointment kept on a second calendar used to be invisible, so a slot
 * already taken could be booked again -- two people arriving for one chair,
 * and nothing anywhere saying why.
 *
 * Deliberately a list the operator writes rather than "read everything on the
 * account". Reading everything is right for a solo practitioner whose own
 * appointments sit on a personal calendar, and wrong for a two-doctor clinic:
 * merging both doctors reports the clinic full when only one is booked, which
 * is the same failure as refusing a free slot. Nothing in a calendar list says
 * which case a business is, so the copy says which one this is for.
 */
function BusyCalendars({ status, onSaved }: { status: Status; onSaved: () => Promise<void> }) {
    const auth = useAuth();
    const saved = status.busy_calendar_ids ?? [];
    const [adding, setAdding] = useState("");
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const save = async (ids: string[]) => {
        setSaving(true);
        setError(null);
        const token = await auth.getAccessToken();
        const result = await calendarFetch("/busy-calendars", token, {
            method: "PUT",
            headers: { "Content-Type": "application/json" },
            body: JSON.stringify({ calendar_ids: ids }),
        });
        if (result.error) setError(result.error);
        else {
            setAdding("");
            await onSaved();
        }
        setSaving(false);
    };

    return (
        <div className="space-y-2 rounded-md border border-border/60 p-3">
            <p className="text-xs font-medium">Also check these calendars</p>
            <p className="text-xs text-muted-foreground">
                Read when deciding if a time is free, so a booking elsewhere is not
                double-booked. Bookings are still created on the calendar above. If
                different people take their own appointments, give each one their own
                agent tool instead &mdash; listing them here would make everyone look
                busy when only one of them is.
            </p>

            {saved.length > 0 ? (
                <ul className="space-y-1">
                    {saved.map((id) => (
                        <li key={id} className="flex items-center justify-between gap-2 text-xs">
                            <span className="truncate font-mono">{id}</span>
                            <Button
                                variant="ghost"
                                size="sm"
                                className="h-6 px-2 text-xs"
                                disabled={saving}
                                onClick={() => void save(saved.filter((c) => c !== id))}
                            >
                                Remove
                            </Button>
                        </li>
                    ))}
                </ul>
            ) : null}

            <div className="flex gap-2">
                <input
                    className="min-w-0 flex-1 rounded-md border border-input bg-background px-2 py-1 text-xs"
                    placeholder="calendar ID, e.g. personal@gmail.com"
                    value={adding}
                    onChange={(e) => setAdding(e.target.value)}
                />
                <Button
                    variant="secondary"
                    size="sm"
                    className="h-7 px-2 text-xs"
                    disabled={saving || !adding.trim()}
                    onClick={() => void save([...saved, adding.trim()])}
                >
                    Add
                </Button>
            </div>
            <p className="text-xs text-muted-foreground">
                In Google Calendar: Settings &rarr; that calendar &rarr; Integrate calendar
                &rarr; Calendar ID.
            </p>
            {error ? <p className="text-xs text-destructive">{error}</p> : null}
        </div>
    );
}

export default GoogleCalendarConnect;
