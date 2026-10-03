import { cn } from "../../lib/cn.js";

/** Eyebrow, title and lead for the top of a section. */
export function SectionHeading({ eyebrow, title, lead, align = "center", className }) {
  return (
    <div
      className={cn(
        "max-w-2xl",
        align === "center" ? "mx-auto text-center" : "text-left",
        className,
      )}
    >
      {eyebrow ? (
        <p className="text-sm font-semibold uppercase tracking-wider text-brand">{eyebrow}</p>
      ) : null}
      {title ? (
        <h2 className="mt-2 text-3xl font-bold sm:text-4xl">{title}</h2>
      ) : null}
      {lead ? <p className="mt-4 text-lg leading-relaxed text-muted">{lead}</p> : null}
    </div>
  );
}
