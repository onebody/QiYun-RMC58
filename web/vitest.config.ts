import { defineConfig } from "vitest/config";
import { configDefaults } from "vitest/config";

export default defineConfig({
  test: {
    environment: "node",
    globals: true,
    // Vitest default exclude doesn't cover our e2e harness; we do that
    // explicitly here so npm test stays pure unit/integration.
    exclude: [...configDefaults.exclude, "e2e/**", "**/*.mjs"],
  },
});
