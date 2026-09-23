'use client';

import { LayoutTemplate, MessageSquareText, PlusIcon } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useState } from 'react';
import { toast } from 'sonner';

import { createWorkflowApiV1WorkflowCreateDefinitionPost } from '@/client/sdk.gen';
import { Button } from "@/components/ui/button";
import {
    Dialog,
    DialogContent,
    DialogDescription,
    DialogHeader,
    DialogTitle,
    DialogTrigger,
} from "@/components/ui/dialog";
import { StartFromTemplate } from '@/components/workflow/StartFromTemplate';
import { useAuth } from '@/lib/auth';
import logger from '@/lib/logger';
import { getRandomId } from '@/lib/utils';

const BLANK_WORKFLOW_DEFINITION = {
    nodes: [
        {
            id: "1",
            type: "startCall",
            position: { x: 175, y: 60 },
            data: {
                prompt: "# Goal\nYou are a helpful agent who is handing a conversation over voice with a human. This is a voice conversation, so transcripts can be error prone.\n\n## Rules\n- Language: UK English but does not have to be correct english\n- Keep responses short and 2-3 sentences max\n- If you have to repeat something that you said in your previous two turns, then rephrase a bit while keeping the same meaning. Never repeat the exact same words as in your previous 2 responses.\n\n## Speech Handling\n- There could be multiple transcription errors. \n- Accept variations: yes/yeah/yep/aye, no/nah/nope\n- If user says \"sorry?\" or \"pardon me\" or \"can you repeat\"  or \"what?\", they might not have heard you- so just repeat what you just said.\n\n### Flow\nStart by saying \"Hi\". Be polite and courteous. ",
                name: "start call",
                allow_interrupt: false,
                invalid: false,
                validationMessage: null,
                add_global_prompt: false,
                delayed_start: false,
                is_start: true,
                selected_through_edge: false,
                hovered_through_edge: false,
                extraction_enabled: false,
                selected: false,
                dragging: false,
            },
        },
    ],
    edges: [],
    viewport: { x: 808, y: 269, zoom: 0.75 },
};

/**
 * The door into a new bot, and which door it is.
 *
 * This button used to open a menu of two: the Agent Builder, which asks
 * eleven questions and then runs a language model to write a flow, and a
 * blank canvas of nodes and edges. Both are the wrong first door. The
 * ready-made agents have existed in the API longer than either screen, are
 * better than anything written on the spot, and open in about a second --
 * and they were offered only to an account with no bots at all, which is
 * exactly the account least able to judge whether it wants one.
 *
 * So: named bots first, by the business they are for. Describing it and
 * starting from nothing stay, underneath, in that order -- describing is
 * the answer when we have no template for your trade, and an empty canvas
 * is the answer when you already know what a node is.
 *
 * A dialog rather than a menu because the templates are cards with a
 * summary, a direction and a language count, and a dropdown row cannot
 * carry that without becoming a card in a menu.
 */
export function CreateWorkflowButton() {
    const router = useRouter();
    const { user, getAccessToken } = useAuth();
    const [isCreating, setIsCreating] = useState(false);
    const [open, setOpen] = useState(false);

    const handleAgentBuilder = () => {
        setOpen(false);
        router.push('/workflow/create');
    };

    const handleBlankCanvas = async () => {
        if (isCreating || !user) return;
        setIsCreating(true);

        try {
            const accessToken = await getAccessToken();
            const name = `Workflow-${getRandomId()}`;
            const response = await createWorkflowApiV1WorkflowCreateDefinitionPost({
                body: {
                    name,
                    workflow_definition: BLANK_WORKFLOW_DEFINITION as unknown as { [key: string]: unknown },
                },
                headers: {
                    'Authorization': `Bearer ${accessToken}`,
                },
            });

            if (response.data?.id) {
                setOpen(false);
                router.push(`/workflow/${response.data.id}`);
            }
        } catch (err) {
            logger.error(`Error creating blank workflow: ${err}`);
            toast.error('Failed to create workflow');
        } finally {
            setIsCreating(false);
        }
    };

    return (
        <Dialog open={open} onOpenChange={setOpen}>
            <DialogTrigger asChild>
                <Button disabled={isCreating}>
                    <PlusIcon className="w-4 h-4" />
                    {isCreating ? 'Creating...' : 'New agent'}
                </Button>
            </DialogTrigger>
            <DialogContent className="max-h-[85vh] overflow-y-auto sm:max-w-2xl">
                <DialogHeader>
                    <DialogTitle>New agent</DialogTitle>
                    <DialogDescription>
                        Pick the one closest to your business. Everything about it is
                        editable afterwards, so the nearest match beats the right answer.
                    </DialogDescription>
                </DialogHeader>

                {/* Renders nothing when the catalogue is empty or unreachable,
                    which is why the two routes below are not conditional on
                    it: a dialog with no way out of it is worse than a
                    dropdown. */}
                <StartFromTemplate />

                <div className="mt-2 space-y-2 border-t pt-4">
                    <button
                        type="button"
                        onClick={handleAgentBuilder}
                        className="flex w-full items-start gap-3 rounded-lg border border-border p-3 text-left transition-colors hover:bg-muted/40"
                    >
                        <MessageSquareText className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                        <span>
                            <span className="block text-sm font-medium">
                                Describe what you want instead
                            </span>
                            <span className="block text-xs text-muted-foreground">
                                For a trade we have no ready-made agent for. A few questions,
                                then we write it.
                            </span>
                        </span>
                    </button>

                    {/* Last, and worded for somebody who already knows what a
                        node is. It was the second of two options on equal
                        footing, which put a graph editor in front of people
                        who wanted a receptionist. */}
                    <button
                        type="button"
                        onClick={handleBlankCanvas}
                        disabled={isCreating}
                        className="flex w-full items-start gap-3 rounded-lg border border-border p-3 text-left transition-colors hover:bg-muted/40 disabled:cursor-wait disabled:opacity-60"
                    >
                        <LayoutTemplate className="mt-0.5 h-4 w-4 shrink-0 text-muted-foreground" />
                        <span>
                            <span className="block text-sm font-medium">
                                Start from an empty canvas
                            </span>
                            <span className="block text-xs text-muted-foreground">
                                One start node and nothing else. Build the flow yourself.
                            </span>
                        </span>
                    </button>
                </div>
            </DialogContent>
        </Dialog>
    );
}
