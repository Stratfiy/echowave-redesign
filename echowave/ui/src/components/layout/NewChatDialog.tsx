'use client';

/**
 * "To:" -- start a chat with one bot, or a group with several.
 *
 * The reference bot desktop opens a new chat with a To: field that searches
 * bots and offers "Create group chat". One bot picked is a direct chat, which
 * we already have (the bot's Chat tab). Two or more picked is a group: a
 * channel is made, named for them unless renamed, and each bot is moved into
 * it, because a channel is exactly a group of bots that answer there and to
 * each other.
 */

import { Check, Search, Users } from 'lucide-react';
import { useRouter } from 'next/navigation';
import { useEffect, useMemo, useRef, useState } from 'react';

import {
    createFolderApiV1FolderPost,
    getWorkflowsApiV1WorkflowFetchGet,
    moveWorkflowToFolderApiV1WorkflowWorkflowIdFolderPut,
} from '@/client/sdk.gen';
import { Button } from '@/components/ui/button';
import { Dialog, DialogContent, DialogDescription, DialogHeader, DialogTitle } from '@/components/ui/dialog';
import { Input } from '@/components/ui/input';
import { detailFromResult } from '@/lib/apiError';
import { useAuth } from '@/lib/auth';
import { cn } from '@/lib/utils';

import { initials } from './SidebarBots';

type Bot = { id: number; name: string };

/** The group's default name: the bots, joined, the way a group chat is titled. */
export function groupName(bots: Bot[]): string {
    return bots.map((b) => b.name).join(', ').slice(0, 100);
}

export function NewChatDialog({ open, onOpenChange }: { open: boolean; onOpenChange: (open: boolean) => void }) {
    const router = useRouter();
    const { user, loading: authLoading } = useAuth();
    const fetched = useRef(false);
    const [bots, setBots] = useState<Bot[]>([]);
    const [query, setQuery] = useState('');
    const [picked, setPicked] = useState<Bot[]>([]);
    const [name, setName] = useState('');
    const [busy, setBusy] = useState(false);
    const [error, setError] = useState<string | null>(null);

    useEffect(() => {
        if (!open || authLoading || !user || fetched.current) return;
        fetched.current = true;
        void (async () => {
            const response = await getWorkflowsApiV1WorkflowFetchGet();
            if (response.error || !response.data) return;
            setBots(response.data.map((w) => ({ id: w.id, name: w.name })));
        })();
    }, [open, authLoading, user]);

    useEffect(() => {
        if (!open) {
            setPicked([]);
            setQuery('');
            setName('');
            setError(null);
        }
    }, [open]);

    const matches = useMemo(() => {
        const q = query.trim().toLowerCase();
        return bots.filter((b) => !q || b.name.toLowerCase().includes(q)).slice(0, 8);
    }, [bots, query]);

    const toggle = (bot: Bot) =>
        setPicked((have) => (have.some((b) => b.id === bot.id) ? have.filter((b) => b.id !== bot.id) : [...have, bot]));

    const start = async () => {
        if (picked.length === 0 || busy) return;
        if (picked.length === 1) {
            onOpenChange(false);
            router.push(`/workflow/${picked[0].id}/thread`);
            return;
        }
        setBusy(true);
        setError(null);
        const created = await createFolderApiV1FolderPost({ body: { name: name.trim() || groupName(picked) } });
        if (created.error || !created.data) {
            setBusy(false);
            setError(detailFromResult(created, 'Could not create the group'));
            return;
        }
        for (const bot of picked) {
            const moved = await moveWorkflowToFolderApiV1WorkflowWorkflowIdFolderPut({
                path: { workflow_id: bot.id },
                body: { folder_id: created.data.id },
            });
            if (moved.error) {
                setBusy(false);
                setError(detailFromResult(moved, `Could not add ${bot.name}`));
                return;
            }
        }
        setBusy(false);
        onOpenChange(false);
        router.push(`/channels/${created.data.id}`);
    };

    return (
        <Dialog open={open} onOpenChange={onOpenChange}>
            <DialogContent className="sm:max-w-md">
                <DialogHeader>
                    <DialogTitle>New chat</DialogTitle>
                    <DialogDescription>
                        One bot opens its chat. Two or more make a group, where they answer you and each other.
                    </DialogDescription>
                </DialogHeader>
                <div className="relative">
                    <Search className="pointer-events-none absolute left-3 top-1/2 h-4 w-4 -translate-y-1/2 text-muted-foreground" />
                    <Input
                        autoFocus
                        aria-label="To"
                        placeholder="To: search bots"
                        value={query}
                        onChange={(e) => setQuery(e.target.value)}
                        className="pl-9"
                    />
                </div>
                <ul className="max-h-64 divide-y divide-border overflow-y-auto rounded-md border border-border" aria-label="Bots">
                    {matches.map((bot) => {
                        const on = picked.some((b) => b.id === bot.id);
                        return (
                            <li key={bot.id}>
                                <button
                                    type="button"
                                    aria-pressed={on}
                                    onClick={() => toggle(bot)}
                                    className={cn('flex w-full items-center gap-3 px-3 py-2 text-left text-sm hover:bg-accent', on && 'bg-accent/60')}
                                >
                                    <span className="flex h-7 w-7 shrink-0 items-center justify-center rounded-md bg-[var(--accent-brand-soft)] text-xs font-semibold text-[var(--accent-brand)]">
                                        {initials(bot.name)}
                                    </span>
                                    <span className="min-w-0 flex-1 truncate">{bot.name}</span>
                                    {on && <Check className="h-4 w-4 text-[var(--accent-brand)]" aria-hidden />}
                                </button>
                            </li>
                        );
                    })}
                    {matches.length === 0 && <li className="px-3 py-3 text-sm text-muted-foreground">No bot called that.</li>}
                </ul>
                {picked.length >= 2 && (
                    <div className="space-y-1">
                        <label htmlFor="new-chat-name" className="text-xs font-medium text-muted-foreground">
                            Group name
                        </label>
                        <Input id="new-chat-name" value={name} placeholder={groupName(picked)} onChange={(e) => setName(e.target.value)} />
                    </div>
                )}
                {error && (
                    <p className="text-sm text-destructive" role="alert">
                        {error}
                    </p>
                )}
                <div className="flex justify-end gap-2">
                    <Button variant="outline" onClick={() => onOpenChange(false)}>
                        Cancel
                    </Button>
                    <Button onClick={() => void start()} disabled={picked.length === 0 || busy}>
                        {picked.length >= 2 ? (
                            <>
                                <Users className="mr-1.5 h-4 w-4" aria-hidden />
                                {busy ? 'Creating…' : 'Create group chat'}
                            </>
                        ) : (
                            'Open chat'
                        )}
                    </Button>
                </div>
            </DialogContent>
        </Dialog>
    );
}

export default NewChatDialog;
