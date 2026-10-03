import { cn } from "../../lib/cn.js";

const VARIANTS = {
  primary:
    "bg-brand text-brand-ink shadow-sm hover:brightness-110 active:brightness-95",
  secondary:
    "bg-surface text-ink ring-1 ring-line hover:bg-canvas",
  ghost: "text-ink hover:bg-ink/5",
  light: "bg-white text-brand shadow-sm hover:bg-white/90",
};

const SIZES = {
  md: "h-11 px-5 text-sm",
  lg: "h-12 px-6 text-base",
};

/** A link or a button that looks like a button. Pass href for a link. */
export function Button({
  href,
  variant = "primary",
  size = "md",
  className,
  children,
  ...props
}) {
  const classes = cn(
    "inline-flex items-center justify-center gap-2 rounded-full font-semibold transition duration-200 disabled:opacity-60",
    VARIANTS[variant],
    SIZES[size],
    className,
  );
  if (href) {
    return (
      <a href={href} className={classes} {...props}>
        {children}
      </a>
    );
  }
  return (
    <button className={classes} {...props}>
      {children}
    </button>
  );
}
