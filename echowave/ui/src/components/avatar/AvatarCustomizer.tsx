"use client";

/**
 * Choose an agent's face: body shape, colour and resting expression, with a
 * live preview. bloub's customiser, in a dialog.
 *
 * Saves to the agent (PUT /workflow/{id}/avatar), so every teammate and every
 * screen sees the same face. "Reset" puts the default face back.
 */

import { Loader2 } from "lucide-react";
import { type ReactNode, useState } from "react";

import { setWorkflowAvatarApiV1WorkflowWorkflowIdAvatarPut } from "@/client/sdk.gen";
import type { AgentAvatar as StoredAvatar } from "@/client/types.gen";
import { Button } from "@/components/ui/button";
import { Dialog, DialogContent, DialogDescription, DialogFooter, DialogHeader, DialogTitle } from "@/components/ui/dialog";
import { detailFromError } from "@/lib/apiError";
import { cn } from "@/lib/utils";

import { AgentAvatar } from "./AgentAvatar";
import {
    type Avatar,
    avatarOf,
    COLOR_LABEL,
    COLOR_OPTIONS,
    EXPRESSION_IDS,
    EXPRESSION_LABEL,
    SHAPE_IDS,
    SHAPE_LABEL,
} from "./avatar";

type Props = {
    workflowId: number;
    name: string;
    avatar: Partial<Avatar> | null | undefined;
    open: boolean;
    onOpenChange: (open: boolean) => void;
    /** The face the server saved; null after a reset. */
    onSaved: (avatar: Avatar | null) => void;
};

export function AvatarCustomizer({ workflowId, name, avatar, open, onOpenChange, onSaved }: Props) {
    const [draft, setDraft] = useState<Avatar>(() => avatarOf(avatar));
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const save = async (next: Avatar | null) => {
        setSaving(true);
        setError(null);
        const response = await setWorkflowAvatarApiV1WorkflowWorkflowIdAvatarPut({
            path: { workflow_id: workflowId },
            body: { avatar: next as StoredAvatar | null },
        });
        setSaving(false);
        if (response.error) {
            setError(detailFromError(response.error, "Could not save the face"));
            return;
        }
        const saved = (response.data?.avatar as Avatar | null | undefined) ?? null;
        onSaved(saved);
        onOpenChange(false);
    };

    const set = (patch: Partial<Avatar>) => setDraft((current) => ({ ...current, ...patch }));

    return (
        <Dialog
            open={open}
            onOpenChange={(next) => {
                if (next) setDraft(avatarOf(avatar));
                onOpenChange(next);
            }}
        >
            <DialogContent className="max-h-[90vh] overflow-y-auto sm:max-w-xl">
                <DialogHeader>
                    <DialogTitle>{name}&apos;s face</DialogTitle>
                    <DialogDescription>Pick a shape, a colour and how it looks at rest. Everyone in the workspace sees it.</DialogDescription>
                </DialogHeader>

                <div className="flex justify-center rounded-2xl bg-muted/40 py-4">
                    <AgentAvatar avatar={draft} size={160} label={`${name}'s face`} />
                </div>

                <fieldset>
                    <legend className="mb-2 text-sm font-medium">Shape</legend>
                    <div className="grid grid-cols-4 gap-2 sm:grid-cols-8">
                        {SHAPE_IDS.map((shape) => (
                            <Choice
                                key={shape}
                                label={SHAPE_LABEL[shape] ?? shape}
                                selected={draft.shape === shape}
                                onSelect={() => set({ shape })}
                            >
                                <AgentAvatar avatar={{ ...draft, shape }} size={40} animate={false} />
                            </Choice>
                        ))}
                    </div>
                </fieldset>

                <fieldset>
                    <legend className="mb-2 text-sm font-medium">Colour</legend>
                    <div className="flex flex-wrap gap-2">
                        {COLOR_OPTIONS.map((color) => (
                            <button
                                key={color.id}
                                type="button"
                                aria-label={COLOR_LABEL[color.id] ?? color.id}
                                aria-pressed={draft.color === color.id}
                                title={COLOR_LABEL[color.id]}
                                onClick={() => set({ color: color.id })}
                                className={cn(
                                    "h-8 w-8 rounded-full border ring-offset-2 ring-offset-background transition",
                                    draft.color === color.id ? "ring-2 ring-foreground" : "hover:scale-110",
                                )}
                                style={{ backgroundColor: color.hex }}
                            />
                        ))}
                    </div>
                </fieldset>

                <fieldset>
                    <legend className="mb-2 text-sm font-medium">Expression</legend>
                    <div className="grid grid-cols-4 gap-2 sm:grid-cols-8">
                        {EXPRESSION_IDS.map((expression) => (
                            <Choice
                                key={expression}
                                label={EXPRESSION_LABEL[expression] ?? expression}
                                selected={draft.expression === expression}
                                onSelect={() => set({ expression })}
                            >
                                <AgentAvatar avatar={{ ...draft, expression }} size={40} animate={false} />
                            </Choice>
                        ))}
                    </div>
                </fieldset>

                {error && (
                    <p role="alert" className="text-sm text-destructive">
                        {error}
                    </p>
                )}

                <DialogFooter className="gap-2 sm:justify-between">
                    <Button variant="ghost" disabled={saving} onClick={() => void save(null)}>
                        Reset to default
                    </Button>
                    <div className="flex gap-2">
                        <Button variant="outline" disabled={saving} onClick={() => onOpenChange(false)}>
                            Cancel
                        </Button>
                        <Button
                            disabled={saving}
                            onClick={() => void save(draft)}
                            aria-label={saving ? "Saving" : undefined}
                        >
                            {saving ? <Loader2 className="h-4 w-4 animate-spin" /> : "Save face"}
                        </Button>
                    </div>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}

function Choice({
    label,
    selected,
    onSelect,
    children,
}: {
    label: string;
    selected: boolean;
    onSelect: () => void;
    children: ReactNode;
}) {
    return (
        <button
            type="button"
            aria-pressed={selected}
            title={label}
            onClick={onSelect}
            className={cn(
                "flex flex-col items-center gap-1 rounded-xl border p-1.5 text-[10px] text-muted-foreground transition",
                selected ? "border-foreground bg-muted text-foreground" : "hover:bg-muted/60",
            )}
        >
            {children}
            <span className="w-full truncate text-center">{label}</span>
        </button>
    );
}
