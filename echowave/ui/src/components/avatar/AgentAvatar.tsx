"use client";

/**
 * An agent's face: bloub's engine (src/lib/bloub) drawn in React.
 *
 * A port of bloub's BloubBot.vue renderer, without its montage player: one
 * state at a time, chosen by the caller (stateForTone), with the shape, colour
 * and resting expression the agent wears. With `follow`, the eyes track the
 * pointer, as Grok's bot does on its first screen.
 *
 * The engine is a pure function of time, so a still face is just
 * `sample(t)` at a fixed t. Faces animate on one shared loop (ticker.ts), pause
 * while off screen, and stay still for anyone who asked for reduced motion.
 */

import { useEffect, useId, useMemo, useRef, useState } from "react";

import { NOTIF_BLUE } from "@/lib/bloub/decor";
import { BotEngine, type BotFrame } from "@/lib/bloub/engine";
import { EXPRESSION_BY_ID } from "@/lib/bloub/expressions";
import { clamp, easings } from "@/lib/bloub/math";
import { DEMI_VIEWBOX, RAYON } from "@/lib/bloub/repere";
import { COLOR_BY_ID, mixHex, SHAPE_BY_ID } from "@/lib/bloub/skins";
import { STATE_BY_ID, type StateId } from "@/lib/bloub/states";
import { cn } from "@/lib/utils";

import { type Avatar, avatarOf, moodForTone, stateForTone } from "./avatar";
import { lookAt, TURN_TIME } from "./gaze";
import { subscribe } from "./ticker";

/** Late enough that every state's arrival has settled: the still frame. */
const STILL_AT = 2.4;

const VB = DEMI_VIEWBOX;

type Props = {
    avatar?: Partial<Avatar> | null;
    /** What the agent is doing (/team/status tone); sets the state and mood. */
    tone?: string | null;
    /** An explicit engine state; overrides `tone`. */
    state?: StateId;
    size?: number;
    /** false draws one still frame, with no loop at all (thumbnails, lists). */
    animate?: boolean;
    /** What shows through the eyes. Transparent suits the states an agent uses
     *  (idle, notify, thinking, sleep), which draw nothing behind the body;
     *  set the surface colour for the orbit-style states, which do. */
    paper?: string;
    /** The eyes follow the pointer (mouse or pen; a finger leaves no cursor). */
    follow?: boolean;
    /** Accessible name; omit for a decorative face beside a visible name. */
    label?: string;
    className?: string;
};

function useReducedMotion(): boolean {
    const [reduced, setReduced] = useState(false);
    useEffect(() => {
        if (typeof window === "undefined" || !window.matchMedia) return;
        const query = window.matchMedia("(prefers-reduced-motion: reduce)");
        setReduced(query.matches);
        const onChange = () => setReduced(query.matches);
        query.addEventListener?.("change", onChange);
        return () => query.removeEventListener?.("change", onChange);
    }, []);
    return reduced;
}

export function AgentAvatar({ avatar, tone, state: explicit, size = 40, animate = true, paper = "transparent", follow = false, label, className }: Props) {
    const face = moodForTone(avatarOf(avatar), tone);
    const state = explicit ?? stateForTone(tone);
    const radii = SHAPE_BY_ID.get(face.shape)?.radii ?? null;
    const expression = EXPRESSION_BY_ID.get(face.expression) ?? null;
    const ink = COLOR_BY_ID.get(face.color)?.hex ?? "#0a0a0c";

    const reduced = useReducedMotion();
    const live = animate && !reduced;

    // The still face: a fresh engine per look, sampled once. Cheap, and it
    // keeps the still path free of any timing.
    const still = useMemo(
        () => (live ? null : new BotEngine(RAYON, state, radii, expression).sample(STILL_AT)),
        [live, state, radii, expression],
    );

    // The moving face: one engine for the component's life, told about changes
    // at the current clock so it morphs into them rather than jumping.
    const engine = useRef<BotEngine | null>(null);
    const clock = useRef(0);
    const [frame, setFrame] = useState<BotFrame | null>(null);
    const svg = useRef<SVGSVGElement | null>(null);
    const [onScreen, setOnScreen] = useState(true);

    if (live && !engine.current) engine.current = new BotEngine(RAYON, state, radii, expression);

    useEffect(() => {
        engine.current?.setShape(radii, clock.current);
    }, [radii]);
    useEffect(() => {
        engine.current?.setExpression(expression, clock.current);
    }, [expression]);
    useEffect(() => {
        engine.current?.setState(state, clock.current);
    }, [state]);

    useEffect(() => {
        const node = svg.current;
        if (!live || !node || typeof IntersectionObserver === "undefined") return;
        const observer = new IntersectionObserver(([entry]) => setOnScreen(entry?.isIntersecting ?? true));
        observer.observe(node);
        return () => observer.disconnect();
    }, [live]);

    // Where the pointer is, in client pixels; null when it left the window.
    const pointer = useRef<{ x: number; y: number } | null>(null);
    useEffect(() => {
        if (!live || !follow) return;
        const move = (event: PointerEvent) => {
            if (event.pointerType !== "touch") pointer.current = { x: event.clientX, y: event.clientY };
        };
        const leave = () => {
            pointer.current = null;
        };
        window.addEventListener("pointermove", move);
        document.addEventListener("pointerleave", leave);
        return () => {
            window.removeEventListener("pointermove", move);
            document.removeEventListener("pointerleave", leave);
        };
    }, [live, follow]);

    useEffect(() => {
        if (!live || !onScreen) return;
        const since = clock.current;
        return subscribe((dt) => {
            const current = engine.current;
            if (!current) return;
            clock.current += dt;
            // Only a resting face follows: elsewhere the gaze is the animation.
            const box = svg.current?.getBoundingClientRect();
            if (follow && box && box.width > 0 && STATE_BY_ID.get(current.state)?.baseFace) {
                const at = pointer.current;
                const nx = at ? clamp((at.x - (box.left + box.width / 2)) / Math.max(1, window.innerWidth / 2), -1, 1) : 0;
                const ny = at ? clamp((at.y - (box.top + box.height / 2)) / Math.max(1, window.innerHeight / 2), -1, 1) : 0;
                const mix = easings.easeOutQuint(clamp((clock.current - since) / TURN_TIME));
                current.setLook(lookAt(nx, ny, mix, at !== null), clock.current);
            }
            setFrame(current.sample(clock.current));
        });
    }, [live, onScreen, follow]);

    const uid = useId().replace(/[^a-zA-Z0-9_-]/g, "");
    const maskId = `face-mask-${uid}`;
    const shown = still ?? frame ?? engine.current?.sample(clock.current) ?? null;
    if (!shown) return null;

    const dotFill = (dot: BotFrame["dots"][number]) =>
        dot.color ?? (dot.depth === undefined || !paper.startsWith("#") ? ink : mixHex(paper, ink, dot.depth));

    const dots = shown.dots.map((dot, i) =>
        dot.d ? (
            <path
                key={i}
                d={dot.d}
                fill={dotFill(dot)}
                opacity={dot.opacity}
                transform={`translate(${dot.x} ${dot.y}) rotate(${dot.rot ?? 0}) scale(${RAYON})`}
            />
        ) : (
            <circle key={i} cx={dot.x} cy={dot.y} r={dot.r} fill={dotFill(dot)} opacity={dot.opacity} />
        ),
    );

    return (
        <svg
            ref={svg}
            width={size}
            height={size}
            viewBox={`${-VB} ${-VB} ${VB * 2} ${VB * 2}`}
            role={label ? "img" : undefined}
            aria-label={label}
            aria-hidden={label ? undefined : true}
            className={cn("shrink-0", className)}
        >
            <defs>
                {/* The eyes are holes in the body, not white shapes on it, so they clip at the edge on their own. */}
                <mask id={maskId} maskUnits="userSpaceOnUse" x={-VB} y={-VB} width={VB * 2} height={VB * 2}>
                    <path d={shown.bodyPath} fill="#fff" />
                    {shown.eyes.map((eye, i) => (
                        <path key={i} d={eye.d} transform={eye.matrix} opacity={eye.alpha} fill="#000" />
                    ))}
                    {shown.notch && <circle cx={shown.notch.x} cy={shown.notch.y} r={shown.notch.r} fill="#000" />}
                </mask>
                {shown.arcs.map((arc) => (
                    <linearGradient
                        key={arc.id}
                        id={`${uid}-${arc.id}`}
                        gradientUnits="userSpaceOnUse"
                        x1={arc.grad.x1}
                        y1={arc.grad.y1}
                        x2={arc.grad.x2}
                        y2={arc.grad.y2}
                    >
                        {arc.grad.stops.map((color, i) => (
                            <stop key={i} offset={i / (arc.grad.stops.length - 1)} stopColor={color} />
                        ))}
                    </linearGradient>
                ))}
            </defs>

            <g fill="none" strokeLinecap="round">
                {shown.arcs.map((arc) => (
                    <path key={`b${arc.id}`} d={arc.back} stroke={`url(#${uid}-${arc.id})`} strokeWidth={arc.width} opacity={arc.opacity} />
                ))}
            </g>

            {shown.dotsBehind && <g>{dots}</g>}

            <g opacity={shown.bodyAlpha}>
                {/* Backing in the surface colour, so a ring passing behind the body does not show through the eyes. */}
                <path d={shown.bodyPath} style={{ fill: paper }} />
                <g mask={`url(#${maskId})`}>
                    <rect x={-VB} y={-VB} width={VB * 2} height={VB * 2} fill={ink} />
                </g>
            </g>

            {!shown.dotsBehind && <g>{dots}</g>}

            {shown.notif && <circle cx={shown.notif.x} cy={shown.notif.y} r={shown.notif.r} fill={NOTIF_BLUE} />}

            <g fill="none" strokeLinecap="round">
                {shown.arcs.map((arc) => (
                    <path key={`f${arc.id}`} d={arc.front} stroke={`url(#${uid}-${arc.id})`} strokeWidth={arc.width} opacity={arc.opacity} />
                ))}
            </g>
        </svg>
    );
}
