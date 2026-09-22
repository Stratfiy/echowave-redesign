'use client';

/**
 * What a run of this bot is likely to cost, what it cannot exceed, and the
 * cap on it (OP-5). Three numbers, from the API, with the assumptions a
 * click away; the cap is the one thing here a person sets.
 */

import { Coins } from 'lucide-react';
import { useEffect, useRef, useState } from 'react';

import {
    setWorkflowSpendCapApiV1WorkflowWorkflowIdSpendCapPut,
    workflowSpendApiV1WorkflowWorkflowIdSpendGet,
} from '@/client/sdk.gen';
import { useAuth } from '@/lib/auth';

type Spend = {
    estimate: {
        per_run_credits: number;
        per_month_credits: number;
        runs_per_month: number;
        hard_maximum_per_run_credits: number;
        notes: string[];
        lines: { what: string; count: number; credits: number; provisional: boolean }[];
    };
    caps: {
        pages_per_run: number;
        script_external_credits_per_run: number | null;
        agent_budget: { credits: number; spent_credits: number } | null;
        budgets_enabled: boolean;
    };
};

export function SpendCard({ workflowId }: { workflowId: number }) {
    const { user, loading: authLoading } = useAuth();
    const fetched = useRef(false);
    const [spend, setSpend] = useState<Spend | null>(null);
    const [cap, setCap] = useState<string>('');
    const [saving, setSaving] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [open, setOpen] = useState(false);

    useEffect(() => {
        if (authLoading || !user || fetched.current) return;
        fetched.current = true;
        void (async () => {
            const res = await workflowSpendApiV1WorkflowWorkflowIdSpendGet({ path: { workflow_id: workflowId } });
            const data = res.data as Spend | undefined;
            if (data) {
                setSpend(data);
                setCap(data.caps.agent_budget ? String(data.caps.agent_budget.credits) : '');
            }
        })();
    }, [authLoading, user, workflowId]);

    if (!spend) return null;
    const { estimate, caps } = spend;

    const save = async () => {
        setSaving(true);
        setError(null);
        const value = cap.trim() === '' ? null : Number(cap);
        const res = await setWorkflowSpendCapApiV1WorkflowWorkflowIdSpendCapPut({
            path: { workflow_id: workflowId },
            body: { credits_per_month: value },
        });
        setSaving(false);
        if (res.error) {
            setError('Only an admin can set the cap.');
            return;
        }
        setSpend(res.data as Spend);
    };

    return (
        <section className="space-y-2" aria-label="Spend">
            <h3 className="flex items-center gap-1.5 text-xs font-semibold uppercase tracking-wider text-muted-foreground">
                <Coins className="h-3.5 w-3.5" aria-hidden />
                Spend
            </h3>
            <p className="text-sm">
                About <span className="font-medium">{estimate.per_run_credits}</span> credits a run
                {estimate.runs_per_month > 0 && (
                    <>
                        , about <span className="font-medium">{estimate.per_month_credits}</span> a month
                    </>
                )}
                . A run cannot exceed{' '}
                <span className="font-medium">{estimate.hard_maximum_per_run_credits}</span>.
            </p>
            <button
                type="button"
                className="text-xs text-muted-foreground underline underline-offset-2"
                onClick={() => setOpen((v) => !v)}
            >
                {open ? 'Hide' : 'Show'} the assumptions
            </button>
            {open && (
                <ul className="space-y-0.5 text-xs text-muted-foreground">
                    {estimate.lines.map((line) => (
                        <li key={line.what}>
                            {line.what}: {line.count} × → {line.credits}
                            {line.provisional ? ' (provisional)' : ''}
                        </li>
                    ))}
                    <li>Page cap: {caps.pages_per_run} a run.</li>
                    {caps.script_external_credits_per_run != null && (
                        <li>Inside a script: {caps.script_external_credits_per_run} credits a run.</li>
                    )}
                    {estimate.notes.map((note) => (
                        <li key={note}>{note}</li>
                    ))}
                </ul>
            )}
            {caps.budgets_enabled ? (
                <div className="flex items-center gap-2 text-sm">
                    <label htmlFor={`cap-${workflowId}`} className="text-muted-foreground">
                        Cap a month
                    </label>
                    <input
                        id={`cap-${workflowId}`}
                        inputMode="numeric"
                        className="w-24 rounded border bg-background px-2 py-1 text-sm"
                        value={cap}
                        placeholder="none"
                        onChange={(e) => setCap(e.target.value.replace(/[^0-9]/g, ''))}
                    />
                    <button
                        type="button"
                        className="rounded border px-2 py-1 text-xs"
                        disabled={saving}
                        onClick={() => void save()}
                    >
                        {saving ? 'Saving…' : 'Set'}
                    </button>
                    {caps.agent_budget && (
                        <span className="text-xs text-muted-foreground">
                            {caps.agent_budget.spent_credits} of {caps.agent_budget.credits} used
                        </span>
                    )}
                </div>
            ) : (
                <p className="text-xs text-muted-foreground">No cap set; spend caps are not enabled here.</p>
            )}
            {error && <p className="text-xs text-[var(--destructive)]">{error}</p>}
        </section>
    );
}

export default SpendCard;
