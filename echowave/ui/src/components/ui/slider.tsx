import * as React from "react"

import { cn } from "@/lib/utils"

/**
 * A range input paired with the value it is set to.
 *
 * Built on the native `<input type="range">` rather than a Radix primitive:
 * the browser already gives it arrow-key and Home/End handling, a correct
 * ARIA role and the value announced to screen readers, so a dependency would
 * buy styling and nothing else.
 *
 * The readout is the point as much as the track. A caller tuning a turn
 * timeout needs to know they are at 0.4s, not somewhere left of centre — so
 * `unit` is rendered next to the number and `hint` carries the sentence that
 * says what moving it does.
 */
function Slider({
  id,
  label,
  value,
  min,
  max,
  step,
  unit,
  hint,
  onValueChange,
  className,
  disabled,
}: {
  id: string
  label: string
  value: number
  min: number
  max: number
  step: number
  unit?: string
  hint?: React.ReactNode
  onValueChange: (value: number) => void
  className?: string
  disabled?: boolean
}) {
  // Range inputs report their value as a string, and a step like 0.05 lands
  // on floats such as 0.30000000000000004. Round to the step's own precision
  // so the readout and the saved value are the number the caller selected.
  const decimals = (String(step).split(".")[1] ?? "").length
  const handle = (raw: string) => {
    const next = Number(raw)
    if (Number.isFinite(next)) onValueChange(Number(next.toFixed(decimals)))
  }

  return (
    <div className={cn("space-y-2", className)} data-slot="slider">
      <div className="flex items-baseline justify-between gap-4">
        <label htmlFor={id} className="text-xs font-medium">
          {label}
        </label>
        <span className="text-xs tabular-nums text-muted-foreground">
          {value.toFixed(decimals)}
          {unit ? <span className="ml-0.5">{unit}</span> : null}
        </span>
      </div>
      <input
        id={id}
        type="range"
        min={min}
        max={max}
        step={step}
        value={value}
        disabled={disabled}
        onChange={(e) => handle(e.target.value)}
        className={cn(
          "h-1.5 w-full cursor-pointer appearance-none rounded-full bg-input",
          "outline-none disabled:pointer-events-none disabled:opacity-50",
          "focus-visible:ring-cta/30 focus-visible:ring-[3px]",
          "[&::-webkit-slider-thumb]:size-4 [&::-webkit-slider-thumb]:appearance-none",
          "[&::-webkit-slider-thumb]:rounded-full [&::-webkit-slider-thumb]:bg-cta",
          "[&::-webkit-slider-thumb]:shadow-sm [&::-webkit-slider-thumb]:transition-transform",
          "[&::-webkit-slider-thumb]:hover:scale-110",
          "[&::-moz-range-thumb]:size-4 [&::-moz-range-thumb]:border-0",
          "[&::-moz-range-thumb]:rounded-full [&::-moz-range-thumb]:bg-cta",
        )}
      />
      {hint ? <p className="text-xs text-muted-foreground">{hint}</p> : null}
    </div>
  )
}

export { Slider }
