"use client";

import { AlertTriangle, CheckCircle2, Loader2, PhoneCall, Trash2 } from "lucide-react";
import { useCallback, useEffect, useRef, useState } from "react";

import {
    connectDialerApiV1DialerConnectionsPost,
    disconnectDialerApiV1DialerConnectionsConnectionIdDelete,
    listDialerConnectionsApiV1DialerConnectionsGet,
    listImportedCallsApiV1DialerConnectionsCallsGet,
} from "@/client/sdk.gen";
import { useConfirm } from "@/components/ConfirmDialog";
import {
    DIALER_CONSENT_CHECKBOX,
    DIALER_CONSENT_LINES,
    DIALER_VENDORS,
    type DialerVendor,
    vendorLabel,
} from "@/components/integrations/dialerConsent";
import { Badge } from "@/components/ui/badge";
import { Button } from "@/components/ui/button";
import { Card, CardContent, CardDescription, CardHeader, CardTitle } from "@/components/ui/card";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import { detailFromResult } from "@/lib/apiError";
import { useAuth } from "@/lib/auth";

interface Connection {
    id: number;
    vendor: string;
    label: string | null;
    key_last_four: string;
    status: string;
    last_error: string | null;
    last_synced_at: string | null;
}

type State = "loading" | "unavailable" | "ready";

/**
 * Connect the dialer a telecalling team already uses, so the call coach can
 * hear their calls (CR-2). One screen, three jobs: say plainly what happens
 * to the calls, take the credentials, and show whether last night worked --
 * a Smartflo token lasts 90 days at most, and an import that has quietly
 * stopped is the failure this screen exists to show.
 *
 * The route is a 404 while the import's flag is off; the screen says it is
 * not available rather than rendering a form that cannot be sent.
 */
export function DialerScreen() {
    const [state, setState] = useState<State>("loading");
    const [connections, setConnections] = useState<Connection[]>([]);
    const [callCount, setCallCount] = useState<number | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [vendor, setVendor] = useState<DialerVendor>(DIALER_VENDORS[0]);
    const [values, setValues] = useState<Record<string, string>>({});
    const [label, setLabel] = useState("");
    const [consent, setConsent] = useState(false);
    const [saving, setSaving] = useState(false);
    const [notice, setNotice] = useState<string | null>(null);
    const { confirm, dialog } = useConfirm();

    const load = useCallback(async () => {
        const result = await listDialerConnectionsApiV1DialerConnectionsGet();
        if (result.error) {
            if (result.response?.status === 404) {
                setState("unavailable");
                return;
            }
            setError(detailFromResult(result, "Could not load your dialer"));
            setState("ready");
            return;
        }
        setConnections(((result.data as { connections?: Connection[] })?.connections) ?? []);
        const calls = await listImportedCallsApiV1DialerConnectionsCallsGet({ query: { days: 7 } });
        if (!calls.error) {
            setCallCount(((calls.data as { calls?: unknown[] })?.calls ?? []).length);
        }
        setState("ready");
    }, []);

    // The auth interceptor attaches the token only once auth has loaded; a
    // request sent before that fails as if the feature were missing.
    const { user, loading: authLoading } = useAuth();
    const hasFetched = useRef(false);
    useEffect(() => {
        if (authLoading || !user || hasFetched.current) return;
        hasFetched.current = true;
        void load();
    }, [authLoading, user, load]);

    const missing = vendor.fields.filter((f) => !f.optional && !(values[f.key] ?? "").trim());
    const canConnect = consent && missing.length === 0 && !saving;

    const connect = async () => {
        setSaving(true);
        setError(null);
        setNotice(null);
        const credentials = Object.fromEntries(
            vendor.fields
                .map((f) => [f.key, (values[f.key] ?? "").trim()])
                .filter(([, v]) => v),
        );
        const result = await connectDialerApiV1DialerConnectionsPost({
            body: {
                vendor: vendor.value,
                credentials,
                label: label.trim() || null,
                consent_accepted: consent,
            },
        });
        setSaving(false);
        if (result.error) {
            setError(detailFromResult(result, "Could not connect the dialer"));
            return;
        }
        const unverified = (result.data as { unverified?: string | null })?.unverified;
        setNotice(
            unverified
                ? `Connected, but ${vendorLabel(vendor.value)} could not be reached just now: ${unverified}`
                : `Connected. Tonight's import will bring in today's recorded calls.`,
        );
        setValues({});
        setLabel("");
        setConsent(false);
        await load();
    };

    const disconnect = async (connection: Connection) => {
        const ok = await confirm({
            title: `Disconnect ${vendorLabel(connection.vendor)}?`,
            description:
                "Every call copied from this dialer is deleted now, and the call coach stops receiving new ones. This cannot be undone.",
            confirmLabel: "Disconnect and delete",
            destructive: true,
        });
        if (!ok) return;
        const result = await disconnectDialerApiV1DialerConnectionsConnectionIdDelete({
            path: { connection_id: connection.id },
        });
        if (result.error) {
            setError(detailFromResult(result, "Could not disconnect"));
            return;
        }
        await load();
    };

    if (state === "loading") {
        return (
            <p className="flex items-center gap-2 text-sm text-muted-foreground">
                <Loader2 className="size-4 animate-spin" /> Loading your dialer…
            </p>
        );
    }
    if (state === "unavailable") {
        return (
            <Card>
                <CardHeader>
                    <CardTitle>Connecting a dialer is not available yet</CardTitle>
                    <CardDescription>
                        When it is, the call coach can hear your team&apos;s calls on Exotel or Tata Smartflo.
                    </CardDescription>
                </CardHeader>
            </Card>
        );
    }

    return (
        <div className="space-y-6">
            {dialog}
            {error && (
                <p role="alert" className="flex items-start gap-2 text-sm text-destructive">
                    <AlertTriangle className="mt-0.5 size-4 shrink-0" /> {error}
                </p>
            )}
            {notice && (
                <p role="status" className="flex items-start gap-2 text-sm">
                    <CheckCircle2 className="mt-0.5 size-4 shrink-0 text-emerald-600" /> {notice}
                </p>
            )}

            {connections.length > 0 && (
                <Card>
                    <CardHeader>
                        <CardTitle>Connected</CardTitle>
                        {callCount !== null && (
                            <CardDescription>
                                {callCount === 1
                                    ? "1 call imported in the last 7 days."
                                    : `${callCount} calls imported in the last 7 days.`}
                            </CardDescription>
                        )}
                    </CardHeader>
                    <CardContent className="space-y-3">
                        {connections.map((c) => (
                            <div
                                key={c.id}
                                className="flex flex-wrap items-start justify-between gap-3 rounded-md border p-3"
                            >
                                <div className="min-w-0 space-y-1">
                                    <p className="font-medium">
                                        {c.label || vendorLabel(c.vendor)}{" "}
                                        <span className="text-sm text-muted-foreground">
                                            {vendorLabel(c.vendor)} · key ending {c.key_last_four}
                                        </span>
                                    </p>
                                    {c.status === "needs_attention" ? (
                                        <Badge variant="destructive">Needs attention</Badge>
                                    ) : (
                                        <Badge variant="success">Connected</Badge>
                                    )}
                                    {c.last_error && <p className="text-sm text-destructive">{c.last_error}</p>}
                                    <p className="text-xs text-muted-foreground">
                                        {c.last_synced_at
                                            ? `Last imported ${new Date(c.last_synced_at).toLocaleString()}`
                                            : "Not imported yet. The first import runs tonight."}
                                    </p>
                                </div>
                                <Button
                                    variant="ghost"
                                    size="sm"
                                    aria-label={`Disconnect ${vendorLabel(c.vendor)}`}
                                    onClick={() => void disconnect(c)}
                                >
                                    <Trash2 className="size-4" />
                                </Button>
                            </div>
                        ))}
                    </CardContent>
                </Card>
            )}

            <Card>
                <CardHeader>
                    <CardTitle className="flex items-center gap-2">
                        <PhoneCall className="size-4" /> Connect your dialer
                    </CardTitle>
                    <CardDescription>
                        For the call coach: it reads your team&apos;s recorded calls and sends each person a short note every evening.
                    </CardDescription>
                </CardHeader>
                <CardContent className="space-y-5">
                    <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="Dialer">
                        {DIALER_VENDORS.map((v) => (
                            <Button
                                key={v.value}
                                type="button"
                                role="radio"
                                aria-checked={vendor.value === v.value}
                                variant={vendor.value === v.value ? "default" : "outline"}
                                size="sm"
                                onClick={() => {
                                    setVendor(v);
                                    setValues({});
                                }}
                            >
                                {v.label}
                            </Button>
                        ))}
                    </div>
                    <p className="text-sm text-muted-foreground">Find these in {vendor.where}.</p>
                    <div className="grid gap-3 sm:grid-cols-2">
                        {vendor.fields.map((f) => (
                            <div key={f.key} className="space-y-1">
                                <Label htmlFor={`dialer-${f.key}`}>
                                    {f.label}
                                    {f.optional ? " (optional)" : ""}
                                </Label>
                                <Input
                                    id={`dialer-${f.key}`}
                                    type={f.secret ? "password" : "text"}
                                    autoComplete="off"
                                    placeholder={f.placeholder}
                                    value={values[f.key] ?? ""}
                                    onChange={(e) => setValues((prev) => ({ ...prev, [f.key]: e.target.value }))}
                                />
                            </div>
                        ))}
                        <div className="space-y-1">
                            <Label htmlFor="dialer-label">Name (optional)</Label>
                            <Input
                                id="dialer-label"
                                placeholder="Sales team"
                                value={label}
                                onChange={(e) => setLabel(e.target.value)}
                            />
                        </div>
                    </div>

                    <div className="space-y-2 rounded-md border bg-muted/40 p-4">
                        <p className="text-sm font-medium">What happens to your calls</p>
                        <ul className="list-disc space-y-1 pl-5 text-sm text-muted-foreground">
                            {DIALER_CONSENT_LINES.map((line) => (
                                <li key={line}>{line}</li>
                            ))}
                        </ul>
                        <label className="flex items-start gap-2 pt-2 text-sm">
                            <input
                                type="checkbox"
                                className="mt-0.5"
                                checked={consent}
                                onChange={(e) => setConsent(e.target.checked)}
                            />
                            <span>{DIALER_CONSENT_CHECKBOX}</span>
                        </label>
                    </div>

                    <Button disabled={!canConnect} onClick={() => void connect()}>
                        {saving && <Loader2 className="mr-2 size-4 animate-spin" />}
                        Connect {vendor.label}
                    </Button>
                </CardContent>
            </Card>
        </div>
    );
}
