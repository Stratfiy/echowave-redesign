"use client";

/**
 * The skills this bot has been taught.
 *
 * They were attachable from the Marketplace shelf and invisible from the
 * bot: you could give a bot eight written procedures and then open the bot
 * and see no trace of them, which makes a prompt that behaves oddly
 * impossible to explain. A bot's own page is where somebody asks "why did
 * it say that", so what it reads belongs here.
 *
 * Read-only on purpose. Attaching is a multi-select over every bot on the
 * shelf and a second way of doing it here would be a second answer to the
 * same question; this points at the shelf instead.
 */

import { BookOpen } from "lucide-react";
import Link from "next/link";
import { useEffect, useState } from "react";

import { skillsOnWorkflowApiV1SkillsOnWorkflowIdGet } from "@/client/sdk.gen";
import type { SkillCard } from "@/client/types.gen";
import { useAuth } from "@/lib/auth";

export function BotSkills({ workflowId }: { workflowId: number }) {
    const { user, loading: authLoading } = useAuth();
    const [skills, setSkills] = useState<SkillCard[] | null>(null);
    const [failed, setFailed] = useState(false);

    useEffect(() => {
        if (authLoading || !user) return;
        let cancelled = false;
        void (async () => {
            const response = await skillsOnWorkflowApiV1SkillsOnWorkflowIdGet({
                path: { workflow_id: workflowId },
            });
            if (cancelled) return;
            if (response.error || !response.data) {
                setFailed(true);
                return;
            }
            setSkills(response.data.skills ?? []);
        })();
        return () => {
            cancelled = true;
        };
    }, [authLoading, user, workflowId]);

    // Nothing at all while it loads: a heading that appears and then empties
    // reads as a glitch on a screen somebody is editing.
    if (skills === null && !failed) return null;

    return (
        <div className="space-y-2" data-testid="bot-skills">
            <div className="flex items-baseline justify-between gap-3">
                <h3 className="text-sm font-medium">Skills it has been taught</h3>
                <Link
                    href="/marketplace/skills"
                    className="text-xs text-muted-foreground underline-offset-4 hover:underline"
                >
                    Teach it another
                </Link>
            </div>
            {failed ? (
                <p className="text-xs text-muted-foreground">
                    Its skills could not be read just now.
                </p>
            ) : skills && skills.length > 0 ? (
                <ul className="space-y-1">
                    {skills.map((skill) => (
                        <li key={skill.slug} className="flex items-start gap-2 text-sm">
                            <span aria-hidden="true" className="mt-0.5 shrink-0">
                                {skill.emoji || <BookOpen className="h-3.5 w-3.5" />}
                            </span>
                            <span className="min-w-0">
                                <span className="font-medium">{skill.title}</span>
                                {skill.description && (
                                    <span className="text-muted-foreground"> — {skill.description}</span>
                                )}
                            </span>
                        </li>
                    ))}
                </ul>
            ) : (
                <p className="text-xs text-muted-foreground">
                    None yet. A skill is a written procedure it follows, on top of the
                    instructions above.
                </p>
            )}
        </div>
    );
}
