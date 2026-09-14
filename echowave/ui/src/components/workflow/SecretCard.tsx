'use client';

/**
 * A bot needs a key; this is where it is typed, and the only place.
 *
 * The chat is the wrong place for a secret: a pasted key sits in the
 * timeline, goes to the model as context, and is seen by everyone in the
 * channel. So the bot's request renders as a form. What is typed goes
 * straight to the account's credentials (admin only, like the Credentials
 * screen), and the card is then stamped with the credential's id and the
 * last four characters -- see services/workflow/secrets_request.py -- so
 * the card that asked is the card that shows it was done.
 */

import { Check, KeyRound } from 'lucide-react';
import { useState } from 'react';

import { provideSecretApiV1TimelineSecretsProvidePost } from '@/client/sdk.gen';
import type { TimelineEvent } from '@/client/types.gen';
import { Button } from '@/components/ui/button';
import { Input } from '@/components/ui/input';
import { Label } from '@/components/ui/label';
import { detailFromError } from '@/lib/apiError';

export type SecretField = { key: string; label: string; secret?: boolean; default?: string };

export type SecretPayload = {
    name?: string;
    why?: string;
    credential_type?: string;
    fields?: SecretField[];
    provided?: {
        credential_uuid?: string;
        credential_name?: string;
        hint?: string;
        by?: number;
        at?: string;
    };
};

export function secretOf(event: TimelineEvent): SecretPayload {
    return (event.payload ?? {}) as SecretPayload;
}

export function SecretCard({
    event,
    onProvided,
}: {
    event: TimelineEvent;
    /** The updated row, so the list holding this card can replace it. */
    onProvided?: (event: TimelineEvent) => void;
}) {
    const request = secretOf(event);
    const fields = request.fields ?? [];
    const [values, setValues] = useState<Record<string, string>>({});
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const ready = fields.every((f) => (values[f.key] ?? '').trim() || f.default);

    const submit = async () => {
        setSaving(true);
        setError(null);
        const result = await provideSecretApiV1TimelineSecretsProvidePost({
            body: { event_id: event.id, values },
        });
        setSaving(false);
        if (result.error) {
            setError(detailFromError(result.error, 'Could not add that'));
            return;
        }
        // Nothing typed here outlives the request. The card re-renders from
        // the stamped row, which carries the handle and never the value.
        setValues({});
        if (result.data) onProvided?.(result.data);
    };

    const provided = request.provided;

    return (
        <div
            className="rounded-lg border border-border bg-card p-4"
            role="group"
            aria-label={request.name ? `Add ${request.name}` : event.summary}
        >
            <p className="flex items-start gap-2 text-sm font-medium">
                <KeyRound aria-hidden className="mt-0.5 h-4 w-4 shrink-0 text-amber-600" />
                <span>{request.name ? `Needs ${request.name}` : event.summary}</span>
            </p>
            {request.why && (
                <p className="mt-1 pl-6 text-sm text-muted-foreground">{request.why}</p>
            )}

            {provided ? (
                <p className="mt-3 flex items-center gap-1.5 pl-6 text-sm">
                    <Check aria-hidden className="h-4 w-4 text-emerald-600" />
                    <span className="font-medium">{provided.credential_name ?? 'Added'}</span>
                    <span className="text-muted-foreground">
                        · added{provided.hint ? `, ends in ${provided.hint}` : ''}
                    </span>
                </p>
            ) : (
                <form
                    className="mt-3 flex flex-col gap-2 pl-6"
                    onSubmit={(e) => {
                        e.preventDefault();
                        void submit();
                    }}
                >
                    <p className="text-xs text-muted-foreground">
                        Typed here, stored in Credentials. It never appears in this chat and the
                        bot only receives its id.
                    </p>
                    {fields.map((field) => {
                        const id = `secret-${event.id}-${field.key}`;
                        return (
                            <div key={field.key} className="flex flex-col gap-1">
                                <Label htmlFor={id} className="text-xs">
                                    {field.label}
                                </Label>
                                <Input
                                    id={id}
                                    type={field.secret ? 'password' : 'text'}
                                    autoComplete="off"
                                    placeholder={field.default ?? ''}
                                    value={values[field.key] ?? ''}
                                    onChange={(e) =>
                                        setValues((v) => ({ ...v, [field.key]: e.target.value }))
                                    }
                                />
                            </div>
                        );
                    })}
                    <div className="mt-1 flex items-center gap-3">
                        <Button type="submit" size="sm" disabled={saving || !ready}>
                            {saving ? 'Adding…' : 'Add securely'}
                        </Button>
                        {error && (
                            <span role="alert" className="text-sm text-destructive">
                                {error}
                            </span>
                        )}
                    </div>
                </form>
            )}
        </div>
    );
}
