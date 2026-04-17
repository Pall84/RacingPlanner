import { defineConfig } from "vite";

// Vite here is purely for env-var injection + dev server.
// The app is plain ES modules; no bundling strictly required, but `vite build`
// produces a clean /dist suitable for Netlify.
export default defineConfig({
  server: {
    port: 5173,
    strictPort: true,
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
