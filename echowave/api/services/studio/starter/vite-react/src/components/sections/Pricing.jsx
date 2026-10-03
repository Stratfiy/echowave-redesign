import { Check } from "lucide-react";

import { cn } from "../../lib/cn.js";
import { Button } from "../ui/Button.jsx";
import { Container, Section } from "../ui/Container.jsx";
import { Reveal } from "../ui/Reveal.jsx";
import { SectionHeading } from "../ui/SectionHeading.jsx";

/** Plans or packages. Prices only ever come from the business. */
export function Pricing({ id = "pricing", eyebrow, title, lead, plans = [] }) {
  if (!plans.length) return null;
  return (
    <Section id={id}>
      <Container>
        <SectionHeading eyebrow={eyebrow} title={title} lead={lead} />
        <div className="mt-12 grid gap-6 lg:grid-cols-3">
          {plans.map((plan, index) => (
            <Reveal key={plan.name} delay={index * 0.06}>
              <div
                className={cn(
                  "flex h-full flex-col rounded-card p-8 ring-1",
                  plan.featured ? "bg-ink text-white ring-ink" : "bg-surface ring-line",
                )}
              >
                <h3 className={cn("text-lg font-semibold", plan.featured && "text-white")}>{plan.name}</h3>
                <p className="mt-4 flex items-baseline gap-1">
                  <span className={cn("font-display text-4xl font-extrabold", plan.featured ? "text-white" : "text-ink")}>
                    {plan.price}
                  </span>
                  {plan.period ? <span className="text-sm opacity-70">{plan.period}</span> : null}
                </p>
                {plan.text ? <p className="mt-3 text-sm opacity-80">{plan.text}</p> : null}
                <ul className="mt-6 flex-1 space-y-3 text-sm">
                  {(plan.features || []).map((feature) => (
                    <li key={feature} className="flex gap-2">
                      <Check className="mt-0.5 h-4 w-4 shrink-0 text-brand" aria-hidden="true" />
                      {feature}
                    </li>
                  ))}
                </ul>
                {plan.cta ? (
                  <Button href={plan.cta.href} variant={plan.featured ? "light" : "primary"} className="mt-8 w-full">
                    {plan.cta.label}
                  </Button>
                ) : null}
              </div>
            </Reveal>
          ))}
        </div>
      </Container>
    </Section>
  );
}
