import { defineConfig } from "vite";

export default defineConfig({
  root: ".",
  build: {
    outDir: "dist",
    target: "es2022",
    minify: "esbuild",
  },
  server: {
    port: 0,
    strictPort: false,
  },
});
