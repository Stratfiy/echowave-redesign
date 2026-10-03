import { Menu, X } from "lucide-react";
import { useEffect, useState } from "react";

import { cn } from "../../lib/cn.js";
import { Button } from "../ui/Button.jsx";
import { Container } from "../ui/Container.jsx";

/** Sticky top bar; turns solid once the page scrolls, collapses on a phone. */
export function Navbar({ brand, links = [], cta }) {
  const [open, setOpen] = useState(false);
  const [scrolled, setScrolled] = useState(false);

  useEffect(() => {
    const onScroll = () => setScrolled(window.scrollY > 8);
    onScroll();
    window.addEventListener("scroll", onScroll, { passive: true });
    return () => window.removeEventListener("scroll", onScroll);
  }, []);

  return (
    <header
      className={cn(
        "sticky top-0 z-40 transition-colors duration-300",
        scrolled || open ? "bg-surface/90 shadow-sm backdrop-blur" : "bg-transparent",
      )}
    >
      <Container className="flex h-16 items-center justify-between gap-4">
        <a href="#top" className="flex items-center gap-2 font-display text-lg font-bold text-ink">
          <span className="grid h-8 w-8 place-items-center rounded-lg bg-brand text-sm text-brand-ink">
            {brand.name.slice(0, 1)}
          </span>
          {brand.name}
        </a>
        <nav aria-label="Main" className="hidden items-center gap-8 md:flex">
          {links.map((link) => (
            <a key={link.href} href={link.href} className="text-sm font-medium text-muted transition hover:text-ink">
              {link.label}
            </a>
          ))}
        </nav>
        <div className="hidden md:block">
          {cta ? <Button href={cta.href}>{cta.label}</Button> : null}
        </div>
        <button
          type="button"
          className="grid h-10 w-10 place-items-center rounded-full text-ink md:hidden"
          aria-label={open ? "Close menu" : "Open menu"}
          aria-expanded={open}
          onClick={() => setOpen((value) => !value)}
        >
          {open ? <X className="h-5 w-5" /> : <Menu className="h-5 w-5" />}
        </button>
      </Container>
      {open ? (
        <nav aria-label="Mobile" className="border-t border-line bg-surface md:hidden">
          <Container className="flex flex-col gap-1 py-3">
            {links.map((link) => (
              <a
                key={link.href}
                href={link.href}
                onClick={() => setOpen(false)}
                className="rounded-lg px-3 py-3 text-base font-medium text-ink hover:bg-canvas"
              >
                {link.label}
              </a>
            ))}
            {cta ? (
              <Button href={cta.href} className="mt-2" onClick={() => setOpen(false)}>
                {cta.label}
              </Button>
            ) : null}
          </Container>
        </nav>
      ) : null}
    </header>
  );
}
