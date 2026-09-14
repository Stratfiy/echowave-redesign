'use client';

/**
 * The input level while the microphone is on: a row of bars, the newest on
 * the right, so a person can see they are being heard before they see the
 * words. Purely a mirror of the meter in useDictation; nothing here decides
 * anything.
 */

import { cn } from '@/lib/utils';

export function Waveform({ levels, className }: { levels: number[]; className?: string }) {
    return (
        <div
            className={cn('flex h-6 items-center gap-[3px]', className)}
            role="img"
            aria-label="Listening"
            data-testid="waveform"
        >
            {levels.map((level, i) => (
                <span
                    key={i}
                    className="w-[3px] rounded-full bg-[var(--accent-brand)] transition-[height] duration-75"
                    style={{ height: `${Math.max(12, Math.round(level * 100))}%` }}
                />
            ))}
        </div>
    );
}

export default Waveform;
