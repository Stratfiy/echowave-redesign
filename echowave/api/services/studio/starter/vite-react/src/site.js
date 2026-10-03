// Everything the page says, in one place. Studio writes this file; the
// sections in src/components/sections render it. Icons are lucide-react
// components (https://lucide.dev/icons), imported by name.
import { CalendarCheck, Clock, HeartHandshake, MessageCircle, ShieldCheck, Sparkles } from "lucide-react";

export const site = {
  brand: { name: __NAME__, tagline: "Friendly, reliable service — ask us anything, any time." },
  nav: [
    { label: "Services", href: "#services" },
    { label: "How it works", href: "#how" },
    { label: "FAQ", href: "#faq" },
    { label: "Contact", href: "#contact" },
  ],
  cta: { label: "Get in touch", href: "#contact" },
  hero: {
    eyebrow: "Open seven days a week",
    title: __NAME__,
    lead: "Tell Studio about your business and this page becomes yours: your services, your story, your colours.",
    primary: { label: "Get in touch", href: "#contact" },
    secondary: { label: "See services", href: "#services" },
    points: ["Replies within minutes", "Talk or chat, day or night"],
    image: null,
  },
  services: {
    eyebrow: "What we do",
    title: "Everything you need, in one place",
    lead: "A short line on why customers choose you.",
    items: [
      { icon: Sparkles, title: "Service one", text: "One or two sentences on what it is and who it is for." },
      { icon: ShieldCheck, title: "Service two", text: "One or two sentences on what it is and who it is for." },
      { icon: HeartHandshake, title: "Service three", text: "One or two sentences on what it is and who it is for." },
    ],
  },
  steps: {
    eyebrow: "How it works",
    title: "Three simple steps",
    items: [
      { title: "Say hello", text: "Message or call us — our assistant answers straight away." },
      { title: "Pick a time", text: "Choose a slot that suits you; we confirm it instantly." },
      { title: "We take care of it", text: "Turn up, and we handle the rest." },
    ],
  },
  stats: [
    { value: "24/7", label: "Always answering", icon: Clock },
    { value: "< 1 min", label: "Average reply", icon: MessageCircle },
    { value: "7 days", label: "Open every week", icon: CalendarCheck },
    { value: "100%", label: "Real people behind it", icon: HeartHandshake },
  ],
  testimonials: { eyebrow: "Reviews", title: "What customers say", items: [] },
  pricing: { eyebrow: "Pricing", title: "Simple, honest pricing", lead: "", plans: [] },
  faq: {
    eyebrow: "FAQ",
    title: "Questions, answered",
    items: [
      { q: "How do I reach you?", a: "Use the chat on this page, call us, or send the form below — whichever is easiest." },
    ],
  },
  closing: {
    title: "Ready when you are",
    lead: "Ask a question or book in under a minute.",
    primary: { label: "Get in touch", href: "#contact" },
  },
  contact: {
    eyebrow: "Contact",
    title: "Talk to us",
    lead: "Leave your details and we will get back to you shortly.",
    phone: "",
    email: "",
    address: "",
  },
  credits: [],
};
