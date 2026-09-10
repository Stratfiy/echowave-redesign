/**
 * Whether a schema field is a range somebody should drag, and what its bounds are.
 *
 * Pulled out of ServiceConfigurationForm so the decision can be tested on its
 * own: it is pure schema arithmetic, and the alternative is asserting slider
 * bounds through a mounted form with a mocked provider catalogue.
 *
 * The rule it encodes: a *bounded* number is a range, and a range is a slider
 * only while dragging is the easier way to set it. max_tokens runs 16-4096,
 * which as a track is four thousand indistinguishable positions, and nobody
 * wants "about 250" -- they want 250.
 */

export interface RangeSchema {
  type?: string;
  default?: string | number | boolean | null;
  minimum?: number;
  maximum?: number;
  exclusiveMinimum?: number;
  exclusiveMaximum?: number;
  anyOf?: RangeSchema[];
}

/** Widest span still worth dragging rather than typing. */
export const MAX_SLIDER_STOPS = 100;

export interface SliderRange {
  min: number;
  max: number;
  step: number;
  /** Where the handle sits before anybody touches it. */
  fallback: number;
  /**
   * True when the field may be left unset and currently is, so the provider's
   * own default applies. The handle still has to sit somewhere, and a
   * position that looks like a chosen value when nothing was chosen is the
   * one thing this must not imply -- so the form says so beside it.
   */
  optional: boolean;
}

/**
 * The numeric half of a schema, looking through Pydantic's nullable wrapper.
 *
 * `float | None` does not emit a bounded number. It emits
 * `anyOf: [{type: number, minimum, maximum}, {type: null}]`, so a reader that
 * only looks at the top level sees no type and no bounds at all. That is why
 * temperature rendered as a bare text box on every provider that declares it
 * optional, while MiniMax and Sarvam -- which declare it as a plain float with
 * a default -- got a slider. Same field, same range, two different controls,
 * decided by a detail of how the Python type was written.
 */
export function numericSchema(
  schema: RangeSchema | undefined,
): { schema: RangeSchema; nullable: boolean } | null {
  if (!schema) return null;
  if (schema.type === "number" || schema.type === "integer") {
    return { schema, nullable: false };
  }
  if (Array.isArray(schema.anyOf)) {
    const numeric = schema.anyOf.find(
      (branch) => branch?.type === "number" || branch?.type === "integer",
    );
    const nullable = schema.anyOf.some((branch) => branch?.type === "null");
    if (numeric) return { schema: numeric, nullable };
  }
  return null;
}

/** A tidy step for this span, rather than an arbitrary fraction of it. */
function stepFor(lower: number, upper: number, defaultValue: unknown): number {
  const span = upper - lower;
  const looksIntegral =
    Number.isInteger(lower) &&
    Number.isInteger(upper) &&
    Number.isInteger(typeof defaultValue === "number" ? defaultValue : 0);
  if (looksIntegral && span >= 4) return 1;
  if (span > 5) return 0.1;
  return span >= 1 ? 0.05 : 0.01;
}

/**
 * The slider for this field, or null if it should stay a box.
 *
 * @param outer the property as the schema declares it, nullable wrapper and all
 */
export function sliderRangeFor(
  outer: RangeSchema | undefined,
): SliderRange | null {
  const found = numericSchema(outer);
  if (!found) return null;

  const { schema, nullable } = found;
  const lower = schema.minimum ?? schema.exclusiveMinimum;
  const upper = schema.maximum ?? schema.exclusiveMaximum;
  if (typeof lower !== "number" || typeof upper !== "number") return null;
  if (upper - lower > MAX_SLIDER_STOPS) return null;

  const declaredDefault = outer?.default ?? schema.default;
  const step = stepFor(lower, upper, declaredDefault);
  // An exclusive bound excludes its own value, so start one step in: a slider
  // that can be dragged to a number the server rejects is worse than one that
  // cannot reach it.
  const min = schema.minimum === undefined ? lower + step : lower;
  const max = schema.maximum === undefined ? upper - step : upper;

  // An optional field with no numeric default has no position that means
  // anything, so the handle starts mid-range and the form says the provider's
  // default is still in force. Starting at the minimum would read as a
  // deliberate zero, which for temperature is a real and very different
  // setting from "unset".
  const unset = declaredDefault === null || declaredDefault === undefined;
  const fallback =
    typeof declaredDefault === "number"
      ? declaredDefault
      : unset && nullable
        ? Math.round(((min + max) / 2) * 100) / 100
        : min;

  return { min, max, step, fallback, optional: nullable && unset };
}
