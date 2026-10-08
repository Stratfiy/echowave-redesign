'use client';

/**
 * Decibyl's private browser, in the thread that asked for it.
 *
 * One panel per task (a `browser_session` row). While the browser is open it
 * shows the page as it is now and the steps so far, and polls both; when it
 * ends, the same panel is the receipt: what it found, what it did, what it
 * would not do and why, and the links.
 *
 * The states are the server's (services/browser/session.py) and each reads
 * as itself -- a CAPTCHA is "Blocked by a CAPTCHA", not "Working". Take over
 * hands the page to the person: clicks on the picture land on the page, and
 * text and keys go straight to it. What they type is sent and not kept.
 *
 * The session is the person's own. Anyone else on the thread gets a 404 from
 * the API, and the panel says whose it is instead of pretending it is empty.
 */

import {
    AlertTriangle,
    ArrowDown,
    ArrowUp,
    Check,
    CircleSlash,
    ExternalLink,
    Globe,
    Hand,
    KeyRound,
    Loader2,
    ShieldAlert,
    Square,
    Trash2,
    User,
    Zap,
} from 'lucide-react';
import { useCallback, useEffect, useRef, useState } from 'react';

import {
    deleteBrowserLoginApiV1BrowserLoginsLoginIdDelete,
    getBrowserScreenApiV1BrowserSessionsSessionUuidScreenGet,
    getBrowserSessionApiV1BrowserSessionsSessionUuidGet,
    handBackBrowserApiV1BrowserSessionsSessionUuidHandbackPost,
    listBrowserLoginsApiV1BrowserLoginsGet,
    sendBrowserInputApiV1BrowserSessionsSessionUuidInputPost,
    stopBrowserApiV1BrowserSessionsSessionUuidStopPost,
    takeOverBrowserApiV1BrowserSessionsSessionUuidTakeoverPost,
} from '@/client/sdk.gen';
import type { BrowserLogin, BrowserScreen, BrowserSessionView, BrowserStep } from '@/client/types.gen';
import { Button } from '@/components/ui/button';
import { detailFromError } from '@/lib/apiError';
import { useAuth } from '@/lib/auth';
import { useFeature } from '@/lib/features';
import { cn } from '@/lib/utils';

const SESSION_POLL_MS = 1500;
const SCREEN_POLL_MS = 1000;
const LIVE = new Set(['starting', 'working', 'waiting_for_you', 'captcha', 'taken_over']);
const KEYS = ['Enter', 'Tab', 'Backspace', 'Escape'] as const;

type Tone = 'busy' | 'attention' | 'good' | 'bad' | 'quiet';

const STATES: Record<string, { label: string; tone: Tone }> = {
    starting: { label: 'Starting', tone: 'busy' },
    working: { label: 'Working', tone: 'busy' },
    waiting_for_you: { label: 'Waiting for you', tone: 'attention' },
    captcha: { label: 'Blocked by a CAPTCHA', tone: 'attention' },
    taken_over: { label: 'You have the browser', tone: 'attention' },
    done: { label: 'Done', tone: 'good' },
    failed: { label: 'Failed', tone: 'bad' },
    stopped: { label: 'Stopped', tone: 'quiet' },
    limit_reached: { label: 'Stopped at its limit', tone: 'quiet' },
};

const TONE_CLASS: Record<Tone, string> = {
    busy: 'border-border bg-muted text-foreground',
    attention: 'border-amber-300 bg-amber-50 text-amber-900 dark:border-amber-700 dark:bg-amber-950 dark:text-amber-100',
    good: 'border-emerald-300 bg-emerald-50 text-emerald-900 dark:border-emerald-700 dark:bg-emerald-950 dark:text-emerald-100',
    bad: 'border-red-300 bg-red-50 text-red-900 dark:border-red-800 dark:bg-red-950 dark:text-red-100',
    quiet: 'border-border bg-muted text-muted-foreground',
};

export function stateOf(state: string): { label: string; tone: Tone } {
    // An unknown state from a newer server still reads as itself.
    return STATES[state] ?? { label: state.replace(/_/g, ' '), tone: 'quiet' };
}

function StepIcon({ kind }: { kind: string }) {
    const cls = 'mt-0.5 h-3.5 w-3.5 shrink-0';
    if (kind === 'refused') return <CircleSlash aria-hidden className={cn(cls, 'text-destructive')} />;
    if (kind === 'warning') return <AlertTriangle aria-hidden className={cn(cls, 'text-amber-600')} />;
    if (kind === 'ask') return <Zap aria-hidden className={cn(cls, 'text-amber-600')} />;
    if (kind === 'done') return <Check aria-hidden className={cn(cls, 'text-emerald-600')} />;
    if (kind === 'person') return <User aria-hidden className={cn(cls, 'text-muted-foreground')} />;
    return <Globe aria-hidden className={cn(cls, 'text-muted-foreground')} />;
}

function placeOf(url: string): string {
    try {
        const parsed = new URL(url);
        const path = parsed.pathname === '/' ? '' : parsed.pathname;
        const shown = `${parsed.host.replace(/^www\./, '')}${path}`;
        return shown.length > 60 ? `${shown.slice(0, 57)}…` : shown;
    } catch {
        return url;
    }
}

function hostOf(url?: string | null): string {
    if (!url) return '';
    try {
        return new URL(url).host.replace(/^www\./, '');
    } catch {
        return url;
    }
}

type Receipt = {
    summary?: string;
    note?: string;
    done?: { label?: string; note?: string; ok?: boolean }[];
    refused?: string[];
    links?: string[];
    kept_logins?: string[];
    used_logins?: string[];
};

export function BrowserPanel({ sessionUuid }: { sessionUuid: string }) {
    const { user, loading: authLoading } = useAuth();
    const browserOn = useFeature('decibyl_browser');
    const [view, setView] = useState<BrowserSessionView | null>(null);
    const [screen, setScreen] = useState<BrowserScreen | null>(null);
    const [missing, setMissing] = useState(false);
    const [error, setError] = useState<string | null>(null);
    const [busy, setBusy] = useState<string | null>(null);
    const [typed, setTyped] = useState('');
    const [keep, setKeep] = useState(false);
    const [logins, setLogins] = useState<BrowserLogin[] | null>(null);
    const [ready, setReady] = useState(false);
    const started = useRef(false);

    const live = view ? LIVE.has(view.state) : true;
    const takenOver = view?.state === 'taken_over';

    const loadSession = useCallback(async () => {
        const response = await getBrowserSessionApiV1BrowserSessionsSessionUuidGet({
            path: { session_uuid: sessionUuid },
        });
        if (response.error) {
            if (response.response?.status === 404) {
                setMissing(true);
                return;
            }
            setError(detailFromError(response.error, 'Could not read the browser'));
            return;
        }
        if (response.data) setView(response.data);
    }, [sessionUuid]);

    const loadScreen = useCallback(async () => {
        const response = await getBrowserScreenApiV1BrowserSessionsSessionUuidScreenGet({
            path: { session_uuid: sessionUuid },
        });
        if (!response.error && response.data) setScreen(response.data);
    }, [sessionUuid]);

    useEffect(() => {
        if (authLoading || !user || started.current) return;
        started.current = true;
        setReady(true);
        void loadSession();
        void loadScreen();
    }, [authLoading, user, loadSession, loadScreen]);

    // Poll while the browser is open; stop once it has ended.
    useEffect(() => {
        if (!ready || missing || !live) return;
        const a = setInterval(() => void loadSession(), SESSION_POLL_MS);
        const b = setInterval(() => void loadScreen(), SCREEN_POLL_MS);
        return () => {
            clearInterval(a);
            clearInterval(b);
        };
    }, [ready, live, missing, loadSession, loadScreen]);

    const press = async (name: string, call: () => Promise<{ error?: unknown }>) => {
        setBusy(name);
        setError(null);
        const result = await call();
        setBusy(null);
        if (result.error) {
            setError(detailFromError(result.error as never, 'The browser did not take that'));
            return;
        }
        void loadSession();
    };

    const path = { session_uuid: sessionUuid };
    const takeOver = () => press('takeover', () => takeOverBrowserApiV1BrowserSessionsSessionUuidTakeoverPost({ path }));
    const stop = () => press('stop', () => stopBrowserApiV1BrowserSessionsSessionUuidStopPost({ path }));
    const handBack = () =>
        press('handback', () =>
            handBackBrowserApiV1BrowserSessionsSessionUuidHandbackPost({ path, body: { keep_login: keep } }),
        );
    const send = (body: Parameters<typeof sendBrowserInputApiV1BrowserSessionsSessionUuidInputPost>[0]['body']) =>
        press('input', async () => {
            const result = await sendBrowserInputApiV1BrowserSessionsSessionUuidInputPost({ path, body });
            void loadScreen();
            return result;
        });

    const clickPicture = (e: React.MouseEvent<HTMLImageElement>) => {
        if (!takenOver) return;
        const box = e.currentTarget.getBoundingClientRect();
        const width = screen?.w || 1280;
        const height = screen?.h || 800;
        const x = Math.round(((e.clientX - box.left) / box.width) * width);
        const y = Math.round(((e.clientY - box.top) / box.height) * height);
        void send({ kind: 'click', x, y });
    };

    const sendTyped = () => {
        if (!typed) return;
        const text = typed;
        // Cleared at once: what was typed is not kept here either.
        setTyped('');
        void send({ kind: 'type', text });
    };

    const loadLogins = async () => {
        const response = await listBrowserLoginsApiV1BrowserLoginsGet();
        if (response.error) {
            setError(detailFromError(response.error, 'Could not read saved logins'));
            return;
        }
        setLogins(response.data?.logins ?? []);
    };
    const forget = async (login: BrowserLogin) => {
        const response = await deleteBrowserLoginApiV1BrowserLoginsLoginIdDelete({ path: { login_id: login.id } });
        if (response.error) {
            setError(detailFromError(response.error, 'Could not forget that login'));
            return;
        }
        setLogins((all) => (all ?? []).filter((l) => l.id !== login.id));
    };

    if (missing && !browserOn) {
        return (
            <div className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground" role="group" aria-label="Private browser">
                <p className="flex items-center gap-2">
                    <ShieldAlert aria-hidden className="h-4 w-4" />
                    The private browser is switched off here.
                </p>
            </div>
        );
    }

    if (missing) {
        return (
            <div className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground" role="group" aria-label="Private browser">
                <p className="flex items-center gap-2">
                    <ShieldAlert aria-hidden className="h-4 w-4" />
                    This browser belongs to the person who asked for it. Only they can see it.
                </p>
            </div>
        );
    }

    if (!view) {
        return (
            <div className="rounded-lg border border-border bg-card p-4 text-sm text-muted-foreground" role="group" aria-label="Private browser">
                {error ? (
                    <p role="alert" className="text-destructive">{error}</p>
                ) : (
                    <p className="flex items-center gap-2" aria-live="polite">
                        <Loader2 aria-hidden className="h-4 w-4 animate-spin" />
                        Opening the browser…
                    </p>
                )}
            </div>
        );
    }

    const state = stateOf(view.state);
    const receipt = (view.receipt ?? null) as Receipt | null;
    const steps: BrowserStep[] = view.steps.slice(-12);
    const site = hostOf(screen?.url) || view.sites[0] || '';

    return (
        <div className="rounded-lg border border-border bg-card" role="group" aria-label="Private browser">
            <div className="flex flex-wrap items-center gap-2 border-b border-border px-4 py-3">
                <Globe aria-hidden className="h-4 w-4 text-[var(--accent-brand)]" />
                <span className="text-sm font-medium">Private browser</span>
                <span
                    className={cn('rounded-full border px-2 py-0.5 text-xs font-medium', TONE_CLASS[state.tone])}
                    data-testid="browser-state"
                >
                    {live && state.tone === 'busy' && (
                        <Loader2 aria-hidden className="mr-1 inline h-3 w-3 animate-spin motion-reduce:animate-none" />
                    )}
                    {state.label}
                </span>
                <span className="ml-auto text-xs text-muted-foreground">
                    Step {view.used.steps ?? 0} of {view.limits.steps ?? '—'} · {view.used.minutes ?? 0} of{' '}
                    {view.limits.minutes ?? '—'} min
                </span>
            </div>

            <div className="space-y-3 p-4">
                <p className="text-sm">{view.task}</p>
                {view.state_note && (
                    <p className="text-sm text-muted-foreground" aria-live="polite">
                        {view.state_note}
                    </p>
                )}

                {live && (
                    <div className="space-y-2">
                        <div className="overflow-hidden rounded-md border border-border bg-muted">
                            <div className="flex items-center gap-2 border-b border-border px-3 py-1.5 text-xs text-muted-foreground">
                                <KeyRound aria-hidden className="h-3 w-3" />
                                <span className="truncate">{site || 'about:blank'}</span>
                                {screen?.title && <span className="truncate">· {screen.title}</span>}
                            </div>
                            {screen?.jpeg ? (
                                // eslint-disable-next-line @next/next/no-img-element
                                <img
                                    src={`data:image/jpeg;base64,${screen.jpeg}`}
                                    alt={takenOver ? 'The page. Click to click on it.' : 'What the browser sees now'}
                                    className={cn('block w-full', takenOver ? 'cursor-crosshair' : 'cursor-default')}
                                    onClick={clickPicture}
                                    data-testid="browser-screen"
                                />
                            ) : (
                                <div className="flex aspect-[16/10] items-center justify-center text-sm text-muted-foreground">
                                    No picture yet
                                </div>
                            )}
                        </div>

                        {takenOver ? (
                            <div className="space-y-2 rounded-md border border-amber-300 p-3 dark:border-amber-700">
                                <p className="text-xs text-muted-foreground">
                                    Click on the page to click there. What you type goes to the page and is not kept.
                                </p>
                                <div className="flex flex-wrap items-center gap-2">
                                    <input
                                        type="password"
                                        autoComplete="off"
                                        aria-label="Type into the page"
                                        placeholder="Type into the page"
                                        value={typed}
                                        onChange={(e) => setTyped(e.target.value)}
                                        onKeyDown={(e) => {
                                            if (e.key === 'Enter') {
                                                e.preventDefault();
                                                sendTyped();
                                            }
                                        }}
                                        className="h-8 min-w-0 flex-1 rounded-md border border-input bg-background px-2 text-sm"
                                    />
                                    <Button size="sm" variant="outline" disabled={!typed || busy !== null} onClick={sendTyped}>
                                        Type it
                                    </Button>
                                </div>
                                <div className="flex flex-wrap items-center gap-1.5">
                                    {KEYS.map((key) => (
                                        <Button
                                            key={key}
                                            size="sm"
                                            variant="outline"
                                            disabled={busy !== null}
                                            onClick={() => void send({ kind: 'key', key })}
                                        >
                                            {key}
                                        </Button>
                                    ))}
                                    <Button
                                        size="sm"
                                        variant="outline"
                                        aria-label="Scroll up"
                                        disabled={busy !== null}
                                        onClick={() => void send({ kind: 'scroll', dy: -500 })}
                                    >
                                        <ArrowUp aria-hidden className="h-3.5 w-3.5" />
                                    </Button>
                                    <Button
                                        size="sm"
                                        variant="outline"
                                        aria-label="Scroll down"
                                        disabled={busy !== null}
                                        onClick={() => void send({ kind: 'scroll', dy: 500 })}
                                    >
                                        <ArrowDown aria-hidden className="h-3.5 w-3.5" />
                                    </Button>
                                </div>
                                <div className="flex flex-wrap items-center gap-3 pt-1">
                                    {view.can_keep_logins && site && (
                                        <label className="flex items-center gap-2 text-sm">
                                            <input type="checkbox" checked={keep} onChange={(e) => setKeep(e.target.checked)} />
                                            Keep me signed in to {site}
                                        </label>
                                    )}
                                    <Button size="sm" disabled={busy !== null} onClick={() => void handBack()}>
                                        {busy === 'handback' ? 'Handing back…' : 'Hand back'}
                                    </Button>
                                </div>
                            </div>
                        ) : (
                            <div className="flex flex-wrap items-center gap-2">
                                <Button size="sm" variant={view.state === 'captcha' ? 'default' : 'outline'} disabled={busy !== null} onClick={() => void takeOver()}>
                                    <Hand aria-hidden className="mr-1 h-3.5 w-3.5" />
                                    {busy === 'takeover' ? 'Taking over…' : 'Take over'}
                                </Button>
                                <Button size="sm" variant="outline" disabled={busy !== null} onClick={() => void stop()}>
                                    <Square aria-hidden className="mr-1 h-3.5 w-3.5" />
                                    {busy === 'stop' ? 'Stopping…' : 'Stop'}
                                </Button>
                                {view.pending_label && (
                                    <span className="text-xs text-muted-foreground">The approval card is in this thread.</span>
                                )}
                            </div>
                        )}
                    </div>
                )}

                {receipt && !live && (
                    <div className="space-y-2 rounded-md border border-border p-3" data-testid="browser-receipt">
                        <p className="text-sm font-medium">{receipt.summary || receipt.note}</p>
                        {(receipt.done ?? []).length > 0 && (
                            <div>
                                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">What it did</p>
                                <ul className="mt-1 space-y-1 text-sm">
                                    {(receipt.done ?? []).map((d, i) => (
                                        <li key={i} className="flex gap-2">
                                            {d.ok === false ? (
                                                <CircleSlash aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0 text-destructive" />
                                            ) : (
                                                <Check aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0 text-emerald-600" />
                                            )}
                                            <span>
                                                {d.label}
                                                {d.note && <span className="text-muted-foreground"> — {d.note}</span>}
                                            </span>
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        )}
                        {(receipt.refused ?? []).length > 0 && (
                            <div>
                                <p className="text-xs font-medium uppercase tracking-wide text-muted-foreground">What it would not do</p>
                                <ul className="mt-1 space-y-1 text-sm">
                                    {(receipt.refused ?? []).map((r, i) => (
                                        <li key={i} className="flex gap-2">
                                            <CircleSlash aria-hidden className="mt-0.5 h-3.5 w-3.5 shrink-0 text-destructive" />
                                            <span>{r}</span>
                                        </li>
                                    ))}
                                </ul>
                            </div>
                        )}
                        {(receipt.links ?? []).length > 0 && (
                            <ul className="flex flex-wrap gap-x-3 gap-y-1 text-sm">
                                {(receipt.links ?? []).map((link) => (
                                    <li key={link}>
                                        <a
                                            href={link}
                                            target="_blank"
                                            rel="noopener noreferrer nofollow"
                                            className="inline-flex items-center gap-1 underline-offset-2 hover:underline"
                                        >
                                            {placeOf(link)}
                                            <ExternalLink aria-hidden className="h-3 w-3" />
                                        </a>
                                    </li>
                                ))}
                            </ul>
                        )}
                        {(receipt.kept_logins ?? []).length > 0 && (
                            <p className="text-xs text-muted-foreground">
                                Kept you signed in to {(receipt.kept_logins ?? []).join(', ')}.
                            </p>
                        )}
                    </div>
                )}

                {steps.length > 0 && (
                    <details open={live} className="text-sm">
                        <summary className="cursor-pointer text-xs font-medium uppercase tracking-wide text-muted-foreground">
                            Steps ({view.steps.length})
                        </summary>
                        <ol className="mt-2 space-y-1" aria-label="Browser steps">
                            {steps.map((step, i) => (
                                <li key={`${step.at}-${i}`} className="flex gap-2">
                                    <StepIcon kind={step.kind} />
                                    <span className={cn(step.kind === 'refused' && 'text-destructive')}>{step.text}</span>
                                </li>
                            ))}
                        </ol>
                    </details>
                )}

                <details
                    className="text-sm"
                    onToggle={(e) => {
                        if ((e.currentTarget as HTMLDetailsElement).open && logins === null) void loadLogins();
                    }}
                >
                    <summary className="cursor-pointer text-xs font-medium uppercase tracking-wide text-muted-foreground">
                        Saved logins
                    </summary>
                    {logins === null ? (
                        <p className="mt-2 text-muted-foreground">Loading…</p>
                    ) : logins.length === 0 ? (
                        <p className="mt-2 text-muted-foreground">
                            {view.can_keep_logins
                                ? 'None. Take over, sign in, and tick “Keep me signed in” to keep one.'
                                : 'Logins cannot be kept on this server yet.'}
                        </p>
                    ) : (
                        <ul className="mt-2 space-y-1">
                            {logins.map((login) => (
                                <li key={login.id} className="flex items-center gap-2">
                                    <KeyRound aria-hidden className="h-3.5 w-3.5 text-muted-foreground" />
                                    <span>{login.site}</span>
                                    <Button
                                        size="sm"
                                        variant="ghost"
                                        aria-label={`Forget ${login.site}`}
                                        onClick={() => void forget(login)}
                                    >
                                        <Trash2 aria-hidden className="h-3.5 w-3.5" />
                                    </Button>
                                </li>
                            ))}
                        </ul>
                    )}
                </details>

                {error && (
                    <p role="alert" className="text-sm text-destructive">
                        {error}
                    </p>
                )}
            </div>
        </div>
    );
}
