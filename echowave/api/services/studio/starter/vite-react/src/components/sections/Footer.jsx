import { Container } from "../ui/Container.jsx";

/** Name, links, and the credits any borrowed photo requires. */
export function Footer({ brand, links = [], credits = [] }) {
  const year = new Date().getFullYear();
  return (
    <footer className="border-t border-line bg-surface py-12">
      <Container className="flex flex-col gap-8 md:flex-row md:items-start md:justify-between">
        <div>
          <p className="font-display text-lg font-bold text-ink">{brand.name}</p>
          {brand.tagline ? <p className="mt-2 max-w-sm text-sm text-muted">{brand.tagline}</p> : null}
        </div>
        <nav aria-label="Footer" className="flex flex-wrap gap-x-6 gap-y-2 text-sm">
          {links.map((link) => (
            <a key={link.href} href={link.href} className="text-muted hover:text-ink">
              {link.label}
            </a>
          ))}
        </nav>
      </Container>
      <Container className="mt-10 space-y-2 text-xs text-muted">
        <p>
          © {year} {brand.name}. All rights reserved.
        </p>
        {credits.length ? (
          <p>
            Photos:{" "}
            {credits.map((credit, index) => (
              <span key={credit.url || index}>
                {index ? "; " : ""}
                <a href={credit.url} className="underline" rel="noopener noreferrer" target="_blank">
                  {credit.text}
                </a>
              </span>
            ))}
          </p>
        ) : null}
      </Container>
    </footer>
  );
}
