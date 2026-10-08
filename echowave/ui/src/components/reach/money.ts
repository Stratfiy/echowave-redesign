/** Paise to "₹1,234" (or "₹1,234.50"), the way the order card says it. */
export function rupees(paise: number | null | undefined): string {
    if (paise == null) return '—';
    const whole = Math.trunc(paise / 100);
    const part = Math.abs(paise % 100);
    return `₹${whole.toLocaleString('en-IN')}${part ? `.${String(part).padStart(2, '0')}` : ''}`;
}
