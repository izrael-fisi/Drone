import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";

export default defineConfig(({ mode }) => ({
  plugins: [react(), mode === "global-view-production" && {
    name: "global-view-production-entry",
    transformIndexHtml: {
      order: "pre",
      handler(html) {
        return html.replace('<html ', '<html data-enterprise="true" ')
          .replace('src="/src/main.tsx"', 'src="/src/main.enterprise.tsx"');
      },
    },
  }],
  clearScreen: false,
  server: {
    port: 1420,
    strictPort: true,
    watch: {
      ignored: ["**/src-tauri/**"],
    },
  },
}));
