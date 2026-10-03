import { Loader2, Mail, MapPin, Phone } from "lucide-react";
import { useState } from "react";

import { FORM_ENDPOINT } from "../../lib/config.js";
import { Button } from "../ui/Button.jsx";
import { Container, Section } from "../ui/Container.jsx";
import { SectionHeading } from "../ui/SectionHeading.jsx";

const FIELD =
  "mt-1 block w-full rounded-xl border-0 bg-canvas px-4 py-3 text-ink ring-1 ring-line placeholder:text-muted/70 focus:ring-2 focus:ring-brand";

/**
 * Contact details and a form. The form posts to the agent Studio connected
 * it to (FORM_ENDPOINT); until then it says so instead of pretending.
 * Sent as text/plain so the browser needs no preflight from any domain.
 */
export function Contact({ id = "contact", eyebrow, title, lead, phone, email, address, fields }) {
  const [state, setState] = useState({ status: "idle", message: "" });
  const wanted = fields || ["name", "phone", "email", "message"];

  async function submit(event) {
    event.preventDefault();
    // Read before the await: React clears currentTarget once the handler yields.
    const form = event.currentTarget;
    if (!FORM_ENDPOINT) {
      setState({ status: "error", message: "This form is not connected yet." });
      return;
    }
    const data = Object.fromEntries(new FormData(form).entries());
    setState({ status: "sending", message: "" });
    try {
      const response = await fetch(FORM_ENDPOINT, {
        method: "POST",
        headers: { "Content-Type": "text/plain;charset=UTF-8" },
        body: JSON.stringify(data),
      });
      if (!response.ok) throw new Error(String(response.status));
      form.reset();
      setState({ status: "sent", message: "Thank you! We will get back to you shortly." });
    } catch {
      setState({ status: "error", message: "That did not go through. Please try again, or call us." });
    }
  }

  return (
    <Section id={id}>
      <Container className="grid gap-12 lg:grid-cols-2">
        <div>
          <SectionHeading eyebrow={eyebrow} title={title} lead={lead} align="left" />
          <ul className="mt-8 space-y-4 text-ink">
            {phone ? (
              <li className="flex items-center gap-3">
                <Phone className="h-5 w-5 text-brand" aria-hidden="true" />
                <a href={`tel:${phone.replace(/\s+/g, "")}`} className="hover:underline">{phone}</a>
              </li>
            ) : null}
            {email ? (
              <li className="flex items-center gap-3">
                <Mail className="h-5 w-5 text-brand" aria-hidden="true" />
                <a href={`mailto:${email}`} className="hover:underline">{email}</a>
              </li>
            ) : null}
            {address ? (
              <li className="flex items-start gap-3">
                <MapPin className="mt-0.5 h-5 w-5 text-brand" aria-hidden="true" />
                <span>{address}</span>
              </li>
            ) : null}
          </ul>
        </div>
        <form onSubmit={submit} className="rounded-card bg-surface p-6 shadow-sm ring-1 ring-line sm:p-8">
          <div className="grid gap-4 sm:grid-cols-2">
            {wanted.includes("name") ? (
              <label className="text-sm font-medium text-ink sm:col-span-2">
                Your name
                <input name="name" required autoComplete="name" className={FIELD} />
              </label>
            ) : null}
            {wanted.includes("phone") ? (
              <label className="text-sm font-medium text-ink">
                Phone
                <input name="phone" type="tel" autoComplete="tel" className={FIELD} />
              </label>
            ) : null}
            {wanted.includes("email") ? (
              <label className="text-sm font-medium text-ink">
                Email
                <input name="email" type="email" autoComplete="email" className={FIELD} />
              </label>
            ) : null}
            {wanted.includes("message") ? (
              <label className="text-sm font-medium text-ink sm:col-span-2">
                How can we help?
                <textarea name="message" rows={4} className={FIELD} />
              </label>
            ) : null}
            {/* Left empty by people, filled by bots: the server drops those. */}
            <input name="website" tabIndex={-1} autoComplete="off" className="hidden" aria-hidden="true" />
          </div>
          <Button type="submit" size="lg" className="mt-6 w-full" disabled={state.status === "sending"}>
            {state.status === "sending" ? <Loader2 className="h-4 w-4 animate-spin" aria-hidden="true" /> : null}
            Send
          </Button>
          {state.message ? (
            <p role="status" className={`mt-4 text-sm ${state.status === "sent" ? "text-brand" : "text-red-600"}`}>
              {state.message}
            </p>
          ) : null}
        </form>
      </Container>
    </Section>
  );
}
