import { Container } from "../ui/Container.jsx";

/** A band of numbers that say why to trust the business. */
export function Stats({ items = [] }) {
  return (
    <section aria-label="In numbers" className="bg-brand py-12 text-brand-ink">
      <Container>
        <dl className="grid grid-cols-2 gap-8 text-center md:grid-cols-4">
          {items.map((item) => (
            <div key={item.label} className="flex flex-col-reverse">
              <dt className="mt-1 text-sm opacity-80">{item.label}</dt>
              <dd className="font-display text-3xl font-extrabold sm:text-4xl">{item.value}</dd>
            </div>
          ))}
        </dl>
      </Container>
    </section>
  );
}
