import { ChevronDown } from "lucide-react";

import { Container, Section } from "../ui/Container.jsx";
import { SectionHeading } from "../ui/SectionHeading.jsx";

/** Questions people ask, as native disclosure widgets: keyboard and screen
 *  reader friendly with no script. */
export function FAQ({ id = "faq", eyebrow, title, items = [] }) {
  if (!items.length) return null;
  return (
    <Section id={id} tone="surface">
      <Container className="max-w-3xl">
        <SectionHeading eyebrow={eyebrow} title={title} />
        <div className="mt-10 divide-y divide-line rounded-card bg-canvas ring-1 ring-line">
          {items.map((item) => (
            <details key={item.q} className="group px-6 py-5">
              <summary className="flex cursor-pointer list-none items-center justify-between gap-4 font-semibold text-ink">
                {item.q}
                <ChevronDown className="h-5 w-5 shrink-0 text-muted transition group-open:rotate-180" aria-hidden="true" />
              </summary>
              <p className="mt-3 leading-relaxed text-muted">{item.a}</p>
            </details>
          ))}
        </div>
      </Container>
    </Section>
  );
}
