import { Contact } from "./components/sections/Contact.jsx";
import { CTA } from "./components/sections/CTA.jsx";
import { FAQ } from "./components/sections/FAQ.jsx";
import { Features } from "./components/sections/Features.jsx";
import { Footer } from "./components/sections/Footer.jsx";
import { Hero } from "./components/sections/Hero.jsx";
import { Navbar } from "./components/sections/Navbar.jsx";
import { Pricing } from "./components/sections/Pricing.jsx";
import { Stats } from "./components/sections/Stats.jsx";
import { Steps } from "./components/sections/Steps.jsx";
import { Testimonials } from "./components/sections/Testimonials.jsx";
import { site } from "./site.js";

export default function App() {
  return (
    <>
      <a href="#main" className="sr-only focus:not-sr-only focus:fixed focus:left-4 focus:top-4 focus:z-50 focus:rounded-lg focus:bg-surface focus:px-4 focus:py-2">
        Skip to content
      </a>
      <Navbar brand={site.brand} links={site.nav} cta={site.cta} />
      <main id="main">
        <Hero {...site.hero} />
        <Features {...site.services} />
        <Stats items={site.stats} />
        <Steps {...site.steps} />
        <Testimonials {...site.testimonials} />
        <Pricing {...site.pricing} />
        <FAQ {...site.faq} />
        <CTA {...site.closing} />
        <Contact {...site.contact} />
      </main>
      <Footer brand={site.brand} links={site.nav} credits={site.credits} />
    </>
  );
}
