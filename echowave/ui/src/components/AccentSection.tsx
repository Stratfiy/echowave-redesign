"use client";

/**
 * Pick the accent colour.
 *
 * Eight checked presets, in the shape people already know from Slack's
 * themes: a row of swatches, the chosen one ringed, and the whole frame --
 * rail, panel, top bar -- takes the colour. No custom picker: a theme is a
 * dozen derived tones that have to hold contrast together, and a colour
 * somebody typed is not one. See lib/accent.ts.
 */

import { Check } from "lucide-react";
import { useEffect, useState } from "react";

import { Button } from "@/components/ui/button";
import {
  type Accent,
  ACCENTS,
  applyAccent,
  readStoredAccent,
  resolveAccent,
  storeAccent,
} from "@/lib/accent";

export function AccentSection() {
  const [accent, setAccent] = useState<Accent>(() => resolveAccent(null));

  // Read on mount, not during render: localStorage does not exist on the
  // server, and reading it in the initial state would make the server and
  // client disagree about which swatch is ringed.
  useEffect(() => {
    const resolved = resolveAccent(readStoredAccent());
    setAccent(resolved);
    // Re-stored on every visit: a theme written by an older build carried
    // only the accent tokens, and the anti-flash script replays what is
    // stored, so the rail would boot on the default until somebody picked
    // again.
    applyAccent(resolved);
    storeAccent(resolved);
  }, []);

  function choose(next: Accent) {
    setAccent(next);
    applyAccent(next);
    storeAccent(next);
  }

  return (
    <div className="space-y-4">
      <div className="flex flex-wrap gap-2" role="radiogroup" aria-label="Theme">
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

      {accent.id !== "coral" ? (
        <Button type="button" variant="ghost" size="sm" onClick={() => choose(ACCENTS[0])}>
          Reset
        </Button>
      ) : null}

      <p className="text-xs text-muted-foreground">
        Colours the frame: the rail, the panel, the top bar, and the links and
        active states inside. Buttons stay ink so they read the same whatever
        you choose. Saved on this device.
      </p>
    </div>
  );
}
