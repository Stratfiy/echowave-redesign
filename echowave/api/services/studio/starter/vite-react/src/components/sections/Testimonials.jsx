import { Quote } from "lucide-react";

import { Container, Section } from "../ui/Container.jsx";
import { Reveal } from "../ui/Reveal.jsx";
import { SectionHeading } from "../ui/SectionHeading.jsx";

/** What customers say. Only ever real quotes the business gave you. */
export function Testimonials({ id = "reviews", eyebrow, title, items = [] }) {
  if (!items.length) return null;
  return (
    <Section id={id} tone="surface">
      <Container>
        <SectionHeading eyebrow={eyebrow} title={title} />
        <div className="mt-12 grid gap-6 md:grid-cols-3">
          {items.map((item, index) => (
            <Reveal key={item.name} delay={index * 0.06}>
              <figure className="flex h-full flex-col rounded-card bg-canvas p-6 ring-1 ring-line">
                <Quote className="h-6 w-6 text-brand" aria-hidden="true" />
                <blockquote className="mt-4 flex-1 leading-relaxed text-ink">“{item.quote}”</blockquote>
                <figcaption className="mt-6 text-sm">
                  <span className="font-semibold text-ink">{item.name}</span>
                  {item.role ? <span className="text-muted"> · {item.role}</span> : null}
                </figcaption>
              </figure>
            </Reveal>
          ))}
        </div>
      </Container>
    </Section>
  );
}
