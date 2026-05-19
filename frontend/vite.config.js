import { defineConfig } from "vite";
import react from "@vitejs/plugin-react";
import { resolve } from "path";

export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    open: "/html/index.html",
  },
  build: {
    rollupOptions: {
      input: {
        index: resolve(__dirname, "html/index.html"),
        articles: resolve(__dirname, "html/articles.html"),
        profile: resolve(__dirname, "html/profile.html"),
      },
    },
  },
});
