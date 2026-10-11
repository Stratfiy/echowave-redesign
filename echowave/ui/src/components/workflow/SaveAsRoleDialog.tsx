"use client";

import { Loader2 } from "lucide-react";
import { useState } from "react";
import { toast } from "sonner";

import { saveWorkspaceRoleApiV1WorkspaceRolesPost } from "@/client/sdk.gen";
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
import { detailFromResult } from "@/lib/apiError";

/**
 * Save an agent as one of the workspace's own roles (MP-2): its steps, its
 * prompts with this business's answers in them, and its settings, as they
 * are now. Hired again from Marketplace → Bots.
 */
export function SaveAsRoleDialog({
    workflowId,
    agentName,
    open,
    onOpenChange,
}: {
    workflowId: number;
    agentName: string;
    open: boolean;
    onOpenChange: (open: boolean) => void;
}) {
    const [name, setName] = useState(agentName);
    const [summary, setSummary] = useState("");
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);

    const save = async () => {
        setSaving(true);
        setError(null);
        const result = await saveWorkspaceRoleApiV1WorkspaceRolesPost({
            body: { workflow_id: workflowId, name: name.trim() || null, summary: summary.trim() || null },
        });
        setSaving(false);
        if (result.error) {
            setError(detailFromResult(result, "Could not save this agent as a role"));
            return;
        }
        toast.success("Saved as one of your workspace's roles. Add it again from Marketplace → Agents.");
        onOpenChange(false);
    };

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent>
                <DialogHeader>
                    <DialogTitle>Save as a workspace role</DialogTitle>
                    <DialogDescription>
                        Its steps, prompts and settings as they are now. Anyone in this workspace can add it
                        again without answering anything twice.
                    </DialogDescription>
                </DialogHeader>
                <div className="space-y-3">
                    <div className="space-y-1">
                        <Label htmlFor="role-name">Name</Label>
                        <Input id="role-name" value={name} onChange={(e) => setName(e.target.value)} />
                    </div>
                    <div className="space-y-1">
                        <Label htmlFor="role-summary">What it does (optional)</Label>
                        <Input
                            id="role-summary"
                            placeholder="Our front desk, with the Adyar clinic's hours"
                            value={summary}
                            onChange={(e) => setSummary(e.target.value)}
                        />
                    </div>
                    {error && (
                        <p role="alert" className="text-sm text-destructive">
                            {error}
                        </p>
                    )}
                </div>
                <DialogFooter>
                    <Button variant="ghost" onClick={() => onOpenChange(false)}>
                        Cancel
                    </Button>
                    <Button disabled={saving} onClick={() => void save()}>
                        {saving && <Loader2 className="mr-2 h-4 w-4 animate-spin" />}
                        Save role
                    </Button>
                </DialogFooter>
            </DialogContent>
        </Dialog>
    );
}
