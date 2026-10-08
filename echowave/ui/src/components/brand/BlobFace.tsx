/**
 * An agent's face in the approved design (October 2026): a soft pastel blob
 * with two black eyes. Every place an agent is shown -- the rail's Recents,
 * the agents grid, the agent's page, the home's waiting card and Today rows
 * -- draws this, so the same agent is the same blob everywhere.
 *
 * The three shapes and three pastels are the design files' own
 * (Sidebar/Agents/Agent/Home.dc.html), copied exactly. Which one an agent
 * wears comes from its id, never its name, so a rename does not repaint it.
 * An agent whose owner picked a face in the customiser keeps that choice's
 * colour (softened to a pastel) and shape family.
 *
 * Always decorative: the agent's name is beside it wherever it is drawn.
 */

import type { Avatar } from "@/components/avatar/avatar";
import { mixHex } from "@/lib/bloub/skins";
import { cn } from "@/lib/utils";

/** The design's blob outlines, viewBox 0 0 100 100. */
export const BLOB_PATHS = [
    "M52 6c18 1 34 12 39 30s-2 39-18 49-39 12-54 2S-1 58 5 40 34 5 52 6z",
    "M60 8c15 5 30 18 31 35 1 18-11 33-26 41s-36 9-48-3S2 47 9 31 45 3 60 8z",
    "M47 5c16-2 33 7 41 22s9 35-1 49-30 21-46 17S9 77 5 60 31 7 47 5z",
] as const;

/** The design's pastels: pink, yellow, green. */
export const BLOB_PASTELS = ["#F7B5E3", "#FFD66B", "#CDEB7A"] as const;

const EYE_INK = "#0d0d0d";

/** A customiser colour, as one of the design's pastels where there is one,
 *  softened toward white otherwise. Ink and cream have no pastel: the seed's
 *  colour is used instead. */
const PASTEL_FOR: Record<string, string> = {
    rose: BLOB_PASTELS[0],
    rouge: BLOB_PASTELS[0],
    ambre: BLOB_PASTELS[1],
    orange: BLOB_PASTELS[1],
    vert: BLOB_PASTELS[2],
    turquoise: BLOB_PASTELS[2],
};
const CUSTOM_HEX: Record<string, string> = {
    brun: "#8b5e3c",
    bleu: "#3b93f0",
    violet: "#8b5cf6",
    gris: "#a3a3a3",
};

/** A stable non-negative number for an id or a name. */
export function blobHash(seed: number | string): number {
    if (typeof seed === "number" && Number.isFinite(seed)) return Math.abs(Math.trunc(seed));
    let h = 2166136261;
    for (const ch of String(seed)) {
        h ^= ch.codePointAt(0) ?? 0;
        h = Math.imul(h, 16777619);
    }
    return Math.abs(h);
}

export type BlobLook = { path: string; fill: string };

/** The shape and pastel an agent wears. Nine looks, so a small roster rarely repeats. */
export function blobLook(seed: number | string, avatar?: Partial<Avatar> | null): BlobLook {
    const n = blobHash(seed) % 9;
    // Colour first, so neighbouring ids (agents made one after another) differ in colour.
    let fill: string = BLOB_PASTELS[n % 3];
    let path: string = BLOB_PATHS[Math.floor(n / 3) % 3];
    if (avatar?.shape && avatar.shape !== "cercle") {
        path = BLOB_PATHS[blobHash(avatar.shape) % 3];
    }
    if (avatar?.color) {
        if (PASTEL_FOR[avatar.color]) fill = PASTEL_FOR[avatar.color];
        else if (CUSTOM_HEX[avatar.color]) fill = mixHex("#ffffff", CUSTOM_HEX[avatar.color], 0.45);
    }
    return { path, fill };
}

export type BlobMood = "awake" | "resting";

export function BlobFace({
    seed,
    avatar,
    size = 26,
    mood = "awake",
    className,
}: {
    /** The agent's id (preferred) or name. */
    seed: number | string;
    /** The agent's stored face, if its owner picked one; null for none. */
    avatar?: Partial<Avatar> | null;
    size?: number;
    /** "resting" closes the eyes (a paused agent). */
    mood?: BlobMood;
    className?: string;
}) {
    const { path, fill } = blobLook(seed, avatar);
    // The design draws slightly smaller eyes on the larger faces (40px and up).
    const [rx, ry] = size >= 40 ? [5, 7] : [6, 8];
    return (
        <svg
            width={size}
            height={size}
            viewBox="0 0 100 100"
            aria-hidden="true"
            focusable="false"
            data-testid="blob-face"
            className={cn("shrink-0", className)}
        >
            <path d={path} fill={fill} />
            {mood === "resting" ? (
                <>
                    <rect x={38 - rx - 1} y={45} width={(rx + 1) * 2} height={3} rx={1.5} fill={EYE_INK} />
                    <rect x={62 - rx - 1} y={45} width={(rx + 1) * 2} height={3} rx={1.5} fill={EYE_INK} />
                </>
            ) : (
                <>
                    <ellipse cx={38} cy={46} rx={rx} ry={ry} fill={EYE_INK} />
                    <ellipse cx={62} cy={46} rx={rx} ry={ry} fill={EYE_INK} />
                </>
            )}
        </svg>
    );
}

export default BlobFace;
