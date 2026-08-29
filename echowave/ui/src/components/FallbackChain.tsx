"use client";

import { Plus, X } from "lucide-react";

import type { ProviderSchema } from "@/components/ServiceConfigurationForm";
import { Button } from "@/components/ui/button";
import { Input } from "@/components/ui/input";
import { Label } from "@/components/ui/label";
import {
    Select,
    SelectContent,
    SelectItem,
    SelectTrigger,
    SelectValue,
} from "@/components/ui/select";

/** One backup in the chain. Thin on purpose — see FallbackServiceConfiguration. */
export interface FallbackService {
    provider: string;
    model?: string;
    voice?: string;
    language?: string;
}

const MAX_BACKUPS = 2;

/** The models a provider suggests, if its schema lists any. */
function modelOptions(schema: ProviderSchema | undefined): string[] {
    const examples = schema?.properties?.model?.examples;
    return Array.isArray(examples) ? examples : [];
}

function providerLabel(name: string, schema: ProviderSchema | undefined): string {
    return schema?.title || name;
}

/**
 * An ordered list of backups for one component.
 *
 * Order is the meaning: the first is tried when the primary fails, the second
 * when the first does. So each row is numbered and the list is not sortable by
 * accident — a chain whose order is unclear is worse than no chain.
 */
export function FallbackChain({
    label,
    description,
    kind,
    schemas,
    value,
    primaryProvider,
    onChange,
}: {
    label: string;
    description: string;
    kind: "tts" | "stt";
    schemas: Record<string, ProviderSchema> | undefined;
    value: FallbackService[];
    /** Shown as already-in-use, so a backup that is the primary reads as a mistake. */
    primaryProvider?: string | null;
    onChange: (next: FallbackService[]) => void;
}) {
    // "decibyl" is a billing arrangement, not a vendor, and the backend refuses
    // it as a backup — so it is not offered here either.
    const providers = Object.keys(schemas ?? {})
        .filter((name) => name !== "decibyl")
        .sort();

    const update = (index: number, patch: Partial<FallbackService>) =>
        onChange(value.map((row, i) => (i === index ? { ...row, ...patch } : row)));

    const remove = (index: number) => onChange(value.filter((_, i) => i !== index));

    const add = () => {
        const firstUnused = providers.find(
            (name) => name !== primaryProvider && !value.some((row) => row.provider === name),
        );
        onChange([...value, { provider: firstUnused ?? providers[0] ?? "" }]);
    };

    return (
        <div className="space-y-3">
            <div>
                <h4 className="text-sm font-medium">{label}</h4>
                <p className="text-xs text-muted-foreground mt-0.5">{description}</p>
            </div>

            {value.length === 0 ? (
                <p className="text-xs text-muted-foreground rounded-md border border-dashed p-3">
                    No backup. If this provider fails mid-call, the caller hears silence.
                </p>
            ) : (
                <div className="space-y-3">
                    {value.map((row, index) => {
                        const schema = schemas?.[row.provider];
                        const models = modelOptions(schema);
                        const isPrimary = Boolean(primaryProvider) && row.provider === primaryProvider;

                        return (
                            <div key={index} className="rounded-md border p-3 space-y-3">
                                <div className="flex items-center justify-between">
                                    <span className="text-xs font-medium text-muted-foreground">
                                        {index === 0 ? "First backup" : "Second backup"}
                                    </span>
                                    <Button
                                        type="button"
                                        variant="ghost"
                                        size="sm"
                                        className="h-7 px-2"
                                        onClick={() => remove(index)}
                                        aria-label={`Remove backup ${index + 1}`}
                                    >
                                        <X className="h-3.5 w-3.5" />
                                    </Button>
                                </div>

                                <div className="grid gap-3 sm:grid-cols-2">
                                    <div className="space-y-1.5">
                                        <Label className="text-xs" htmlFor={`${kind}-fallback-${index}-provider`}>
                                            Provider
                                        </Label>
                                        <Select
                                            value={row.provider}
                                            onValueChange={(provider) =>
                                                // Clearing the model matters: a model name
                                                // belongs to the provider that was chosen,
                                                // and carrying it over sends the new one a
                                                // name it has never heard of.
                                                update(index, { provider, model: "" })
                                            }
                                        >
                                            <SelectTrigger id={`${kind}-fallback-${index}-provider`}>
                                                <SelectValue placeholder="Select a provider" />
                                            </SelectTrigger>
                                            <SelectContent>
                                                {providers.map((name) => (
                                                    <SelectItem key={name} value={name}>
                                                        {providerLabel(name, schemas?.[name])}
                                                    </SelectItem>
                                                ))}
                                            </SelectContent>
                                        </Select>
                                    </div>

                                    {models.length > 0 && (
                                        <div className="space-y-1.5">
                                            <Label className="text-xs" htmlFor={`${kind}-fallback-${index}-model`}>
                                                Model
                                            </Label>
                                            <Select
                                                value={row.model || ""}
                                                onValueChange={(model) => update(index, { model })}
                                            >
                                                <SelectTrigger id={`${kind}-fallback-${index}-model`}>
                                                    <SelectValue placeholder="Provider default" />
                                                </SelectTrigger>
                                                <SelectContent>
                                                    {models.map((model) => (
                                                        <SelectItem key={model} value={model}>
                                                            {model}
                                                        </SelectItem>
                                                    ))}
                                                </SelectContent>
                                            </Select>
                                        </div>
                                    )}

                                    {kind === "tts" && (
                                        <div className="space-y-1.5">
                                            <Label className="text-xs" htmlFor={`${kind}-fallback-${index}-voice`}>
                                                Voice
                                            </Label>
                                            <Input
                                                id={`${kind}-fallback-${index}-voice`}
                                                placeholder="Provider default"
                                                value={row.voice || ""}
                                                onChange={(e) => update(index, { voice: e.target.value })}
                                            />
                                        </div>
                                    )}
                                </div>

                                {isPrimary && (
                                    <p className="text-xs text-destructive">
                                        This is the provider already in use. A backup on the same
                                        provider fails at the same moment it does.
                                    </p>
                                )}
                            </div>
                        );
                    })}
                </div>
            )}

            {value.length < MAX_BACKUPS && (
                <Button
                    type="button"
                    variant="outline"
                    size="sm"
                    onClick={add}
                    disabled={providers.length === 0}
                >
                    <Plus className="h-3.5 w-3.5 mr-1.5" />
                    Add backup
                </Button>
            )}
        </div>
    );
}
