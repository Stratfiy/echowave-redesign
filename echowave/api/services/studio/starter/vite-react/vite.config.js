import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import tailwindcss from "@tailwindcss/vite";

// base: "./" keeps every asset URL relative, so the built site works from
// any path -- the Studio preview, a sub-folder, or the root of a domain.
export default defineConfig({
  plugins: [react(), tailwindcss()],
  base: "./",
});
