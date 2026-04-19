import { defineConfig } from "vite";

// Vite here is for env-var injection, dev server, and a dev-only proxy
// mirroring Netlify's production proxy (see netlify.toml). Both proxies
// let frontend code use relative URLs like fetch("/api/fitness/summary")
// — the request is forwarded to the backend transparently. Same-origin
// requests mean cookies are first-party, which keeps Safari ITP from
// silently dropping the session cookie after OAuth.
export default defineConfig({
  server: {
    port: 5173,
    strictPort: true,
    proxy: {
      "/api": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
      "/auth": {
        target: "http://localhost:8000",
        changeOrigin: true,
      },
    },
  },
  build: {
    outDir: "dist",
    sourcemap: true,
  },
});
