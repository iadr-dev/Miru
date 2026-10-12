import path from "node:path";
import { defineConfig } from "vitest/config";

export default defineConfig({
  resolve: {
    alias: {
      "@": path.resolve(__dirname, "src"),
    },
  },
  test: {
    environment: "node",
    // next-intl's ESM build imports `next/navigation` without an extension, which Node's resolver
    // rejects; inlining lets Vite resolve it (component tests that render a chat view need it).
    server: { deps: { inline: ["next-intl"] } },
  },
});
