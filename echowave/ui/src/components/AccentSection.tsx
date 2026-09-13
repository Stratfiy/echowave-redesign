"use client";

/**
 * Pick the accent colour.
 *
 * Eight checked presets and a custom picker, in the shape people already know
 * from ChatGPT's appearance settings: a row of swatches, the chosen one ringed.
 *
 * The custom colour is *validated*, not just accepted. A colour too pale to
 * clear 3:1 on white would erase every focus ring in the product while looking
 * fine to the person who chose it — so it is refused, with the reason said out
 * loud rather than the swatch quietly not taking. See lib/accent.ts for why an
 * accent is a pair of colours and not one.
 */

import { Check } from "lucide-react";
import { useEffect, useId, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  type Accent,
  ACCENTS,
  applyAccent,
  contrastOnWhite,
  deepen,
  MIN_BRIGHT_CONTRAST,
  readStoredAccent,
  resolveAccent,
  storeAccent,
} from "@/lib/accent";

const PRESET_IDS = new Set(ACCENTS.map((a) => a.id));

export function AccentSection() {
  const customInputId = useId();
  const [accent, setAccent] = useState<Accent>(() => resolveAccent(null));
  const [custom, setCustom] = useState("#e15b35");
  const [tooPale, setTooPale] = useState<string | null>(null);

  // Read on mount, not during render: localStorage does not exist on the
  // server, and reading it in the initial state would make the server and
  // client disagree about which swatch is ringed.
  useEffect(() => {
    const resolved = resolveAccent(readStoredAccent());
    setAccent(resolved);
    if (!PRESET_IDS.has(resolved.id)) setCustom(resolved.bright);
  }, []);

  function choose(next: Accent) {
    setAccent(next);
    setTooPale(null);
    applyAccent(next);
    storeAccent(next);
  }

  function chooseCustom(value: string) {
    setCustom(value);
    const contrast = contrastOnWhite(value);
    if (contrast < MIN_BRIGHT_CONTRAST) {
      // Say what is wrong and leave the current accent alone. Applying it
      // anyway is how a focus ring disappears without anybody noticing.
      setTooPale(
        `${value.toUpperCase()} is too pale to hold a focus ring — it scores ` +
          `${contrast.toFixed(1)}:1 on white, and ${MIN_BRIGHT_CONTRAST}:1 is the floor.`,
      );
      return;
    }
    choose({ id: value, label: "Custom", bright: value, deep: deepen(value) });
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="Accent colour">
        {ACCENTS.map((option) => {
          const selected = option.id === accent.id;
          return (
            <button
              key={option.id}
              type="button"
              role="radio"
              aria-checked={selected}
              aria-label={option.label}
              title={option.label}
              onClick={() => choose(option)}
              className="flex h-9 w-9 items-center justify-center rounded-full ring-offset-2 transition-shadow focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-ring focus-visible:ring-offset-2"
              style={{
                backgroundColor: option.bright,
                boxShadow: selected ? `0 0 0 2px #ffffff inset, 0 0 0 2px ${option.deep}` : undefined,
              }}
            >
              {selected ? <Check className="h-4 w-4 text-white" aria-hidden="true" /> : null}
            </button>
          );
        })}
      </div>

      <div className="flex flex-wrap items-center gap-3">
        <label htmlFor={customInputId} className="text-sm text-muted-foreground">
          Or pick your own
        </label>
        <input
          id={customInputId}
          type="color"
          value={custom}
          onChange={(event) => chooseCustom(event.target.value)}
          className="h-9 w-14 cursor-pointer rounded-md border border-border bg-transparent p-1"
        />
        <span className="font-mono text-xs text-muted-foreground">{custom.toUpperCase()}</span>
        {accent.id !== "coral" ? (
          <Button
            type="button"
            variant="ghost"
            size="sm"
            onClick={() => choose(ACCENTS[0])}
          >
            Reset
          </Button>
        ) : null}
      </div>

      {tooPale ? (
        <p role="alert" className="text-sm text-destructive">
          {tooPale}
        </p>
      ) : null}

      <p className="text-xs text-muted-foreground">
        The accent colours links, focus rings and active states. Buttons stay
        ink so they read the same whatever you choose. Saved on this device.
      </p>
    </div>
  );
}
