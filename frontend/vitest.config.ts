// How the frontend test suite is run.

import react from "@vitejs/plugin-react";
import { defineConfig } from "vitest/config";
import path from "path";

export default defineConfig({
  plugins: [react()],

  oxc: { jsx: { runtime: "automatic" } },

  resolve: {
    alias: {
      "@": path.resolve(__dirname, "."),
    },
  },

  test: {
    environment: "jsdom",

    setupFiles: ["./tests/setup.ts"],

    globals: true,

    include: ["tests/**/*.test.{ts,tsx}"],

    coverage: {
      provider: "v8",
      include: ["lib/**", "components/**", "app/**"],
    },
  },
});
