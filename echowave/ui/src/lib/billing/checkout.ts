type RazorpayOptions = {
    key: string;
    amount: number;
    currency: string;
    order_id: string;
    name: string;
    description: string;
    handler: () => void;
    modal: { ondismiss: () => void };
    theme: { color: string };
};

declare global {
    interface Window {
        Razorpay?: new (options: RazorpayOptions) => { open: () => void };
    }
}

const CHECKOUT_SRC = "https://checkout.razorpay.com/v1/checkout.js";
let pending: Promise<void> | null = null;

/** Share concurrent loads, bound slow networks, and allow retries after failure. */
export function loadCheckout(): Promise<void> {
    if (typeof window === "undefined") return Promise.reject(new Error("Payment checkout needs a browser."));
    if (window.Razorpay) return Promise.resolve();
    if (pending) return pending;
    pending = new Promise<void>((resolve, reject) => {
        // A previous attempt may have failed before listeners were attached.
        document.querySelector<HTMLScriptElement>(`script[src="${CHECKOUT_SRC}"]`)?.remove();
        const script = document.createElement("script");
        script.src = CHECKOUT_SRC;
        script.async = true;
        const finish = (error?: Error) => {
            clearTimeout(timer);
            script.onload = null;
            script.onerror = null;
            if (error) { script.remove(); reject(error); } else resolve();
        };
        const timer = setTimeout(() => finish(new Error("The payment window took too long to load. Please try again.")), 15000);
        script.onload = () => finish(window.Razorpay ? undefined : new Error("The payment window did not load. Please try again."));
        script.onerror = () => finish(new Error("Could not load the payment window. Please try again."));
        document.body.appendChild(script);
    }).finally(() => { pending = null; });
    return pending;
}
