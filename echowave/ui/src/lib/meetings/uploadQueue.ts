/**
 * Segments go up one at a time, in order, and survive a bad connection.
 *
 * A failed send is retried with backoff; while the browser says it is
 * offline the queue waits instead of spending attempts. A segment that still
 * cannot be sent is given up and reported -- the server then records it as a
 * part that never arrived, which is what the record shows. Nothing is
 * pretended: a lost part is a gap, not a silent skip.
 */

export type QueueItem<T> = { key: string; value: T };

export type UploadQueueOptions<T> = {
    send: (value: T) => Promise<"ok" | "refused" | "retry">;
    onGiveUp?: (value: T) => void;
    onChange?: (pending: number) => void;
    delays?: number[];
    sleep?: (ms: number) => Promise<void>;
    isOnline?: () => boolean;
};

const DEFAULT_DELAYS = [1000, 2000, 4000, 8000];

export class UploadQueue<T> {
    private items: QueueItem<T>[] = [];
    private running = false;
    private idle: Array<() => void> = [];
    private readonly opts: UploadQueueOptions<T>;

    constructor(opts: UploadQueueOptions<T>) {
        this.opts = opts;
    }

    get pending(): number {
        return this.items.length + (this.running ? 1 : 0);
    }

    enqueue(key: string, value: T) {
        this.items.push({ key, value });
        this.opts.onChange?.(this.pending);
        void this.pump();
    }

    private sleep(ms: number) {
        return this.opts.sleep ? this.opts.sleep(ms) : new Promise<void>((r) => setTimeout(r, ms));
    }

    private online() {
        if (this.opts.isOnline) return this.opts.isOnline();
        return typeof navigator === "undefined" || navigator.onLine !== false;
    }

    private async sendOne(value: T): Promise<boolean> {
        const delays = this.opts.delays ?? DEFAULT_DELAYS;
        let attempt = 0;
        let offlineWaits = 0;
        while (true) {
            if (!this.online() && offlineWaits < 120) {
                offlineWaits += 1;
                await this.sleep(1000);
                continue;
            }
            let outcome: "ok" | "refused" | "retry";
            try {
                outcome = await this.opts.send(value);
            } catch {
                outcome = "retry";
            }
            if (outcome === "ok") return true;
            if (outcome === "refused" || attempt >= delays.length) return false;
            await this.sleep(delays[attempt]);
            attempt += 1;
        }
    }

    private async pump() {
        if (this.running) return;
        this.running = true;
        while (this.items.length > 0) {
            const item = this.items.shift()!;
            this.opts.onChange?.(this.pending);
            const sent = await this.sendOne(item.value);
            if (!sent) this.opts.onGiveUp?.(item.value);
        }
        this.running = false;
        this.opts.onChange?.(0);
        for (const resolve of this.idle.splice(0)) resolve();
    }

    /** Resolves when everything queued has been sent or given up, or after
     *  ``timeoutMs``; true when it drained. */
    async drained(timeoutMs: number): Promise<boolean> {
        if (!this.running && this.items.length === 0) return true;
        return new Promise<boolean>((resolve) => {
            const timer = setTimeout(() => resolve(false), timeoutMs);
            this.idle.push(() => {
                clearTimeout(timer);
                resolve(true);
            });
        });
    }
}
