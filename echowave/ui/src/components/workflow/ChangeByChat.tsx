"use client";

/**
 * The pencil beside a bot's own wording: change it by saying what you want.
 *
 * The builder chat has existed for months and was rendered nowhere, so the
 * only way to change what a bot says was to edit the prompt box by hand --
 * which assumes the author can write a system prompt, and the people buying
 * this cannot and should not have to.
 *
 * Nothing here can break a live bot. The chat's editing tools write a draft
 * and a person publishes, which is the same boundary the rest of the product
 * draws, so the worst outcome of a bad turn is a draft somebody discards.
 *
 * It does not reload the editor for you. The chat writes straight to the
 * draft while the form beside it is still holding the old one, and a form
 * that saved itself afterwards would put the old text back. So the change is
 * announced and the reload is a press, which is also the moment somebody
 * decides whether they wanted it.
 */

import { Pencil } from "lucide-react";
import { useState } from "react";

import { AgentBuilderPanel } from "@/components/agent-builder/AgentBuilderPanel";
import { Button } from "@/components/ui/button";
import {
    Sheet,
    SheetContent,
    SheetDescription,
    SheetHeader,
    SheetTitle,
    SheetTrigger,
} from "@/components/ui/sheet";

/** The tools that mean this bot's draft has just been rewritten. */
const REVISING = new Set(["revise_agent_prompt", "revise_agent_facts"]);

export function ChangeByChat({
    name,
    onRevised,
}: {
    /** The bot being changed, named in the opening message so the chat does
     *  not start by asking which one. */
    name: string;
    /** Called the first time a turn changes this bot's draft. */
    onRevised: () => void;
}) {
    const [open, setOpen] = useState(false);

    return (
        <Sheet open={open} onOpenChange={setOpen}>
            <SheetTrigger asChild>
                <Button variant="outline" size="sm">
                    <Pencil className="mr-2 h-4 w-4" aria-hidden />
                    Change by chat
                </Button>
            </SheetTrigger>
            <SheetContent side="right" className="flex w-full flex-col sm:max-w-lg">
                <SheetHeader>
                    <SheetTitle>Change {name}</SheetTitle>
                    <SheetDescription>
                        Say what it should do differently. Changes are saved as a draft —
                        the live bot keeps answering exactly as it does now until you
                        publish.
                    </SheetDescription>
                </SheetHeader>
                <div className="min-h-0 flex-1 overflow-y-auto">
                    <AgentBuilderPanel
                        showSuggestions={false}
                        prefill={{ text: `I want to change my bot "${name}". `, nonce: 1 }}
                        onActions={(actions) => {
                            if (actions.some((action) => REVISING.has(action))) {
                                onRevised();
                            }
                        }}
                    />
                </div>
            </SheetContent>
        </Sheet>
    );
}
