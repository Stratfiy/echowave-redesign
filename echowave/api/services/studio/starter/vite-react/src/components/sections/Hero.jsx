import { Check } from "lucide-react";

import { Button } from "../ui/Button.jsx";
import { Container } from "../ui/Container.jsx";
import { Reveal } from "../ui/Reveal.jsx";

/**
 * The first screen. With an `image` it splits text and picture; without one
 * it centres the text on a soft brand gradient.
 */
export function Hero({ eyebrow, title, lead, primary, secondary, points = [], image }) {
  const text = (
    <Reveal className={image ? "" : "mx-auto max-w-3xl text-center"}>
      {eyebrow ? (
        <p className="inline-flex items-center gap-2 rounded-full bg-brand/10 px-3 py-1 text-sm font-semibold text-brand">
          {eyebrow}
        </p>
      ) : null}
      <h1 className="mt-5 text-4xl font-extrabold leading-[1.05] sm:text-5xl lg:text-6xl">{title}</h1>
      {lead ? <p className="mt-6 text-lg leading-relaxed text-muted sm:text-xl">{lead}</p> : null}
      <div className={`mt-8 flex flex-wrap gap-3 ${image ? "" : "justify-center"}`}>
        {primary ? (
          <Button href={primary.href} size="lg">
            {primary.label}
          </Button>
        ) : null}
        {secondary ? (
          <Button href={secondary.href} size="lg" variant="secondary">
            {secondary.label}
          </Button>
        ) : null}
      </div>
      {points.length ? (
        <ul className={`mt-8 flex flex-wrap gap-x-6 gap-y-2 text-sm text-muted ${image ? "" : "justify-center"}`}>
          {points.map((point) => (
            <li key={point} className="flex items-center gap-2">
              <Check className="h-4 w-4 text-brand" aria-hidden="true" />
              {point}
            </li>
          ))}
        </ul>
      ) : null}
    </Reveal>
  );

  return (
    <section
      id="top"
      className="relative overflow-hidden bg-gradient-to-b from-brand/10 via-canvas to-canvas pb-16 pt-12 sm:pb-24 sm:pt-20"
    >
      <div
        aria-hidden="true"
        className="pointer-events-none absolute -top-40 left-1/2 h-[480px] w-[880px] -translate-x-1/2 rounded-full bg-brand/15 blur-3xl"
      />
      <Container className="relative">
        {image ? (
          <div className="grid items-center gap-12 lg:grid-cols-2">
            {text}
            <Reveal delay={0.1}>
              <img
                src={image.src}
                alt={image.alt}
                className="aspect-[4/3] w-full rounded-card object-cover shadow-2xl ring-1 ring-line"
                loading="eager"
              />
            </Reveal>
          </div>
        ) : (
          text
        )}
      </Container>
    </section>
  );
}
