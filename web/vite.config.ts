import { defineConfig } from "vite";

// VITE_SERVER_URL is baked in at build time; see fly.toml for the server side.
export default defineConfig({
  build: { target: "es2022", outDir: "dist" },
});
