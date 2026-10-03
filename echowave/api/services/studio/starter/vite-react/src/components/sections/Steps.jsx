import { Container, Section } from "../ui/Container.jsx";
import { Reveal } from "../ui/Reveal.jsx";
import { SectionHeading } from "../ui/SectionHeading.jsx";

/** How it works, as numbered steps. */
export function Steps({ id = "how", eyebrow, title, lead, items = [] }) {
  return (
    <Section id={id}>
      <Container>
        <SectionHeading eyebrow={eyebrow} title={title} lead={lead} />
        <ol className="mt-12 grid gap-6 md:grid-cols-3">
          {items.map((item, index) => (
            <Reveal key={item.title} delay={index * 0.08}>
              <li className="relative h-full rounded-card bg-surface p-6 ring-1 ring-line">
                <span className="font-display text-4xl font-extrabold text-brand/25">
                  {String(index + 1).padStart(2, "0")}
                </span>
                <h3 className="mt-3 text-lg font-semibold">{item.title}</h3>
                <p className="mt-2 leading-relaxed text-muted">{item.text}</p>
              </li>
            </Reveal>
          ))}
        </ol>
      </Container>
    </Section>
  );
}
