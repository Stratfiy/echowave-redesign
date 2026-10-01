"use client";

/**
 * One of the 3D pictures (src/lib/art.ts), as decoration: no alt text, out
 * of the accessibility tree. Callers can drop this in anywhere, server
 * components included.
 */

import { art3d, type ArtName } from "@/lib/art";
import { cn } from "@/lib/utils";

export function Art3D({ name, size, className }: { name: ArtName; size: number; className?: string }) {
    return <ArtImage name={name} size={size} className={className} />;
}

/** The picture itself. */
export function ArtImage({ name, size, className }: { name: ArtName; size: number; className?: string }) {
    return (
        // A local, already-sized webp: next/image would only add a wrapper.
        // eslint-disable-next-line @next/next/no-img-element
        <img
            src={art3d(name)}
            alt=""
            aria-hidden="true"
            width={size}
            height={size}
            loading="lazy"
            draggable={false}
            data-art={name}
            className={cn("shrink-0 select-none object-contain", className)}
            style={{ width: size, height: size }}
        />
    );
}
