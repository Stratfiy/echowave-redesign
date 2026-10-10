"use client";

import { rememberHire } from "@/lib/hireResume";

/** Hire, for a visitor who may not have an account yet. Remembers the role
 *  so the landing after signup resumes its hire flow, then goes to signup. */
export function HireButton({ templateId, name }: { templateId: string; name: string }) {
    return (
        <a
            href="/auth/signup"
            onClick={() => rememberHire(templateId)}
            className="inline-flex h-10 items-center rounded-md bg-[var(--accent-brand)] px-5 text-sm font-medium text-white"
        >
            Add {name}
        </a>
    );
}
