"use client";

/**
 * The credentials a tool authenticates with, and the two things you could not
 * previously do to one: change it, or get rid of it.
 *
 * `POST /api/v1/credentials/` had a screen from the day tools shipped. `PUT`
 * and `DELETE` never did, so a secret pasted into this product could be
 * created and then only ever created again under a different name. The day one
 * leaks — the reason rotation exists at all — the answer was a support ticket,
 * and the leaked value stayed live in the meantime.
 *
 * **Rotation replaces the secret, not the reference.** `webhook_deliveries`
 * re-resolves `credential_uuid` at send time (see `db/models.py`), so every
 * tool pointing at this credential picks the new value up on its next call
 * with nothing to reconfigure. That is what makes rotating safe enough to be a
 * one-click operation and worth saying on the screen — an operator who thinks
 * they have to re-wire every tool will not rotate.
 *
 * **The server never echoes a secret back.** `CredentialResponse` carries name,
 * description and type and nothing else, so the rotate form opens empty rather
 * than pre-filled. Empty is honest: there is nothing to show, and a masked
 * placeholder would imply we could show it.
 *
 * Delete is a soft delete and it is **not** reference-checked — nothing stops
 * you removing a credential a live tool still names, and that tool will start
 * failing to authenticate. Hence the confirm step saying so in those words.
 */

import { AlertCircle, KeyRound, Loader2, RotateCw, Trash2 } from "lucide-react";
import { useCallback, useEffect, useState } from "react";

import {
    deleteCredentialApiV1CredentialsCredentialUuidDelete,
    listCredentialsApiV1CredentialsGet,
    updateCredentialApiV1CredentialsCredentialUuidPut,
} from "@/client";
import type { CredentialResponse, WebhookCredentialType } from "@/client/types.gen";
import { CreateCredentialDialog } from "@/components/http/create-credential-dialog";
import { Button } from "@/components/ui/button";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogFooter,
    DialogHeader,
    DialogTitle,
} from "@/components/ui/dialog";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";
import { Skeleton } from "@/components/ui/skeleton";
import { useAccessRoles } from "@/hooks/useAccessRoles";
import { detailFromError } from "@/lib/apiError";
import { CREDENTIAL_TYPES, credentialFields } from "@/lib/credentials/fields";

const TYPE_LABEL: Record<string, string> = Object.fromEntries(
    CREDENTIAL_TYPES.map((t) => [t.value, t.label]),
);

export function CredentialsSection() {
    const { isOrganizationAdmin, loaded: rolesLoaded } = useAccessRoles();
    const [credentials, setCredentials] = useState<CredentialResponse[] | null>(null);
    const [error, setError] = useState<string | null>(null);
    const [adding, setAdding] = useState(false);
    const [rotating, setRotating] = useState<CredentialResponse | null>(null);
    const [deleting, setDeleting] = useState<CredentialResponse | null>(null);
    const [busy, setBusy] = useState(false);

    const refresh = useCallback(async () => {
        const result = await listCredentialsApiV1CredentialsGet();
        if (result.error) {
            setError(detailFromError(result.error, "Could not load credentials"));
            setCredentials([]);
            return;
        }
        setError(null);
        setCredentials(result.data ?? []);
    }, []);

    useEffect(() => {
        void refresh();
    }, [refresh]);

    const remove = useCallback(async () => {
        if (!deleting) return;
        setBusy(true);
        const result = await deleteCredentialApiV1CredentialsCredentialUuidDelete({
            path: { credential_uuid: deleting.uuid },
        });
        setBusy(false);
        if (result.error) {
            setError(detailFromError(result.error, "Could not delete the credential"));
            return;
        }
        setDeleting(null);
        await refresh();
    }, [deleting, refresh]);

    if (credentials === null) {
        return <Skeleton className="h-24 w-full" />;
    }

    return (
        <div className="space-y-4">
            {error && (
                <p className="flex items-start gap-2 text-sm text-red-600 dark:text-red-400">
                    <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                    <span>{error}</span>
                </p>
            )}

            {credentials.length === 0 ? (
                <p className="text-sm text-muted-foreground">
                    No credentials yet. Tools that call an authenticated API need one.
                </p>
            ) : (
                <ul className="divide-y rounded-lg border">
                    {credentials.map((credential) => (
                        <li
                            key={credential.uuid}
                            className="flex items-center justify-between gap-3 p-3"
                        >
                            <div className="min-w-0">
                                <p className="flex items-center gap-2 truncate text-sm font-medium">
                                    <KeyRound className="h-3.5 w-3.5 shrink-0 text-muted-foreground" />
                                    {credential.name}
                                </p>
                                <p className="truncate text-xs text-muted-foreground">
                                    {TYPE_LABEL[credential.credential_type] ??
                                        credential.credential_type}
                                    {credential.description
                                        ? ` — ${credential.description}`
                                        : ""}
                                </p>
                            </div>
                            {rolesLoaded && isOrganizationAdmin && (
                                <div className="flex shrink-0 gap-1">
                                    <Button
                                        type="button"
                                        variant="outline"
                                        size="sm"
                                        onClick={() => setRotating(credential)}
                                    >
                                        <RotateCw className="mr-1.5 h-3.5 w-3.5" />
                                        Rotate
                                    </Button>
                                    <Button
                                        type="button"
                                        variant="ghost"
                                        size="sm"
                                        aria-label={`Delete ${credential.name}`}
                                        onClick={() => setDeleting(credential)}
                                    >
                                        <Trash2 className="h-3.5 w-3.5 text-red-600" />
                                    </Button>
                                </div>
                            )}
                        </li>
                    ))}
                </ul>
            )}

            {rolesLoaded && !isOrganizationAdmin ? (
                // Readable, not hidden: a member wiring up a tool needs to know
                // which credentials exist and what to ask for by name.
                <p className="rounded-md bg-muted px-3 py-2 text-xs text-muted-foreground">
                    Only an organization Admin or Owner can add, rotate or delete
                    these. Ask one of them.
                </p>
            ) : (
                <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={() => setAdding(true)}
                >
                    Add credential
                </Button>
            )}

            <CreateCredentialDialog
                open={adding}
                onOpenChange={setAdding}
                onCreated={() => void refresh()}
            />

            <RotateCredentialDialog
                credential={rotating}
                onOpenChange={(open) => !open && setRotating(null)}
                onRotated={() => {
                    setRotating(null);
                    void refresh();
                }}
            />

            <Dialog
                open={deleting !== null}
                onOpenChange={(open) => !open && setDeleting(null)}
            >
                <DialogContent className="sm:max-w-md">
                    <DialogHeader>
                        <DialogTitle>Delete {deleting?.name}?</DialogTitle>
                        <DialogDescription>
                            Any tool still using this credential will start failing to
                            authenticate on its next call. Nothing checks for that
                            first, so make sure no tool names it. If you only want to
                            change the secret, rotate it instead — every tool keeps
                            working.
                        </DialogDescription>
                    </DialogHeader>
                    <DialogFooter>
                        <Button
                            variant="outline"
                            onClick={() => setDeleting(null)}
                            disabled={busy}
                        >
                            Keep it
                        </Button>
                        <Button variant="destructive" onClick={() => void remove()} disabled={busy}>
                            {busy ? (
                                <>
                                    <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                    Deleting…
                                </>
                            ) : (
                                "Delete"
                            )}
                        </Button>
                    </DialogFooter>
                </DialogContent>
            </Dialog>
        </div>
    );
}

function RotateCredentialDialog({
    credential,
    onOpenChange,
    onRotated,
}: {
    credential: CredentialResponse | null;
    onOpenChange: (open: boolean) => void;
    onRotated: () => void;
}) {
    const [type, setType] = useState<WebhookCredentialType>("bearer_token");
    const [data, setData] = useState<Record<string, string>>({});
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);

    // Reopening on a different credential must not carry the previous one's
    // half-typed secret into this form.
    useEffect(() => {
        if (credential) {
            setType(credential.credential_type as WebhookCredentialType);
            setData({});
            setError(null);
        }
    }, [credential]);

    const fields = credentialFields(type);
    // Every field, not just the secret ones. A bearer token sent without its
    // header name is a credential the server stores and the tool cannot use.
    const complete = fields.length > 0 && fields.every((f) => (data[f.key] ?? "").trim());

    const save = useCallback(async () => {
        if (!credential) return;
        setSaving(true);
        setError(null);
        const result = await updateCredentialApiV1CredentialsCredentialUuidPut({
            path: { credential_uuid: credential.uuid },
            // Type and data travel together: the server only validates the data
            // against the type when both are present, so sending the secret
            // alone would let a bearer token be stored under basic_auth.
            body: { credential_type: type, credential_data: data },
        });
        setSaving(false);
        if (result.error) {
            setError(detailFromError(result.error, "Could not rotate the credential"));
            return;
        }
        onRotated();
    }, [credential, type, data, onRotated]);

    return (
        <Dialog open={credential !== null} onOpenChange={onOpenChange}>
            <DialogContent className="sm:max-w-md">
                <DialogHeader>
                    <DialogTitle>Rotate {credential?.name}</DialogTitle>
                    <DialogDescription>
                        Enter the new secret. Every tool using this credential picks
                        it up on its next call — there is nothing to reconfigure. The
                        old value stops working immediately.
                    </DialogDescription>
                </DialogHeader>

                {error && (
                    <p className="flex items-start gap-2 rounded-md border border-red-200 bg-red-50 p-3 text-sm text-red-600 dark:border-red-900/50 dark:bg-red-950/30 dark:text-red-400">
                        <AlertCircle className="mt-0.5 h-4 w-4 shrink-0" />
                        <span>{error}</span>
                    </p>
                )}

                <div className="space-y-4 py-2">
                    <div className="grid gap-2">
                        <Label>Credential Type</Label>
                        <Select
                            value={type}
                            onValueChange={(v) => {
                                setType(v as WebhookCredentialType);
                                setData({});
                            }}
                        >
                            <SelectTrigger>
                                <SelectValue />
                            </SelectTrigger>
                            <SelectContent>
                                {CREDENTIAL_TYPES.map((t) => (
                                    <SelectItem key={t.value} value={t.value}>
                                        {t.label}
                                    </SelectItem>
                                ))}
                            </SelectContent>
                        </Select>
                    </div>

                    {fields.map((field) => (
                        <div key={field.key} className="grid gap-2">
                            <Label htmlFor={`rotate-${field.key}`}>{field.label}</Label>
                            <Input
                                id={`rotate-${field.key}`}
                                type={field.isSecret ? "password" : "text"}
                                autoComplete="off"
                                value={data[field.key] ?? ""}
                                onChange={(e) =>
                                    setData((prev) => ({
                                        ...prev,
                                        [field.key]: e.target.value,
                                    }))
                                }
                                placeholder={field.placeholder}
                            />
                        </div>
                    ))}
                </div>

                <DialogFooter>
                    <Button
                        variant="outline"
                        onClick={() => onOpenChange(false)}
                        disabled={saving}
                    >
                        Cancel
                    </Button>
                    <Button onClick={() => void save()} disabled={!complete || saving}>
                        {saving ? (
                            <>
                                <Loader2 className="mr-2 h-4 w-4 animate-spin" />
                                Rotating…
                            </>
                        ) : (
                            "Rotate"
                        )}
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
