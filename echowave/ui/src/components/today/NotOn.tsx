"use client";

import Link from "next/link";

import { EmptyState } from "@/components/EmptyState";

/** What a Today page says while its switch is off for this workspace:
 *  the truth, and the way back. */
export function NotOn({ what }: { what: string }) {
    return (
        <div className="mx-auto w-full max-w-[640px] px-4 py-8">
            <EmptyState title={`${what} is not switched on here yet`} description={<Link href="/tasks" className="underline underline-offset-2">Back to Today</Link>} />
        </div>
    );
}

export default NotOn;
