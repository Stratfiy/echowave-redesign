import { cn } from "../../lib/cn.js";

/** The page's measure: 16px gutters on a phone, a centred column on a desk. */
export function Container({ className, children }) {
  return (
    <div className={cn("mx-auto w-full max-w-6xl px-4 sm:px-6 lg:px-8", className)}>
      {children}
    </div>
  );
}

/** A full-width band with vertical rhythm. `tone` sets its background. */
export function Section({ id, tone = "canvas", className, children }) {
  const tones = {
    canvas: "bg-canvas",
    surface: "bg-surface",
    brand: "bg-brand text-brand-ink",
    ink: "bg-ink text-white",
  };
  return (
    <section id={id} className={cn("scroll-mt-20 py-16 sm:py-24", tones[tone], className)}>
      {children}
    </section>
  );
}
