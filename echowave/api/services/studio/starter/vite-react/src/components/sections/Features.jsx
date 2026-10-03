import { Container, Section } from "../ui/Container.jsx";
import { Reveal } from "../ui/Reveal.jsx";
import { SectionHeading } from "../ui/SectionHeading.jsx";

/** A grid of what the business offers, each with an icon. */
export function Features({ id = "services", eyebrow, title, lead, items = [] }) {
  return (
    <Section id={id} tone="surface">
      <Container>
        <SectionHeading eyebrow={eyebrow} title={title} lead={lead} />
        <div className="mt-12 grid gap-6 sm:grid-cols-2 lg:grid-cols-3">
          {items.map((item, index) => {
            const Icon = item.icon;
            return (
              <Reveal key={item.title} delay={index * 0.05}>
                <article className="h-full rounded-card bg-canvas p-6 ring-1 ring-line transition hover:-translate-y-0.5 hover:shadow-lg">
                  {Icon ? (
                    <span className="grid h-11 w-11 place-items-center rounded-xl bg-brand/10 text-brand">
                      <Icon className="h-5 w-5" aria-hidden="true" />
                    </span>
                  ) : null}
                  <h3 className="mt-5 text-lg font-semibold">{item.title}</h3>
                  <p className="mt-2 leading-relaxed text-muted">{item.text}</p>
                </article>
              </Reveal>
            );
          })}
        </div>
      </Container>
    </Section>
  );
}
