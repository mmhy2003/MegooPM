import { defineConfig } from "vitest/config";

export default defineConfig({
  // esbuild reads `jsx: "react-jsx"` from tsconfig, so JSX transpiles with the
  // automatic runtime without the babel-based @vitejs/plugin-react (which
  // conflicts with shadcn's babel pin).
  resolve: {
    tsconfigPaths: true,
  },
  test: {
    environment: "jsdom",
    globals: true,
    setupFiles: ["./vitest.setup.ts"],
    include: ["src/**/*.{test,spec}.{ts,tsx}"],
    css: false,
  },
});
