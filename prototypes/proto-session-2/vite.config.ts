import { defineConfig } from "vite";

export default defineConfig({
  root: ".",
  publicDir: "public",
  build: {
    outDir: "dist",
    target: "es2022",
    cssMinify: true,
    minify: "esbuild",
  },
  server: {
    port: 0,
    strictPort: false,
  },
});
