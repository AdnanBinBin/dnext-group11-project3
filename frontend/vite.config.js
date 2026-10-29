import { defineConfig } from "vite";
import vue from "@vitejs/plugin-vue";

// config.js (URL de l'API) n'est pas bundlé : il est écrit au déploiement à côté du build,
// pour que le même build serve quel que soit l'environnement.
export default defineConfig({
  plugins: [vue()],
  server: { port: 8080, strictPort: true, host: true },
});
