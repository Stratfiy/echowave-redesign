import { Button } from "../ui/Button.jsx";
import { Container } from "../ui/Container.jsx";
import { Reveal } from "../ui/Reveal.jsx";

/** The closing ask, on a strong brand panel. */
export function CTA({ title, lead, primary, secondary }) {
  return (
    <section className="py-16 sm:py-24">
      <Container>
        <Reveal>
          <div className="relative overflow-hidden rounded-card bg-brand px-6 py-14 text-center text-brand-ink sm:px-12">
            <div aria-hidden="true" className="absolute -right-24 -top-24 h-72 w-72 rounded-full bg-white/10 blur-2xl" />
            <h2 className="relative mx-auto max-w-2xl text-3xl font-bold text-brand-ink sm:text-4xl">{title}</h2>
            {lead ? <p className="relative mx-auto mt-4 max-w-xl text-lg opacity-90">{lead}</p> : null}
            <div className="relative mt-8 flex flex-wrap justify-center gap-3">
              {primary ? (
                <Button href={primary.href} variant="light" size="lg">
                  {primary.label}
                </Button>
              ) : null}
              {secondary ? (
                <Button href={secondary.href} variant="ghost" size="lg" className="text-brand-ink hover:bg-white/10">
                  {secondary.label}
                </Button>
              ) : null}
            </div>
          </div>
        </Reveal>
      </Container>
    </section>
  );
}
