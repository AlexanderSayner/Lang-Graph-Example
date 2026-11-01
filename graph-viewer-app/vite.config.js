import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import { resolve } from 'path';

export default defineConfig({
  plugins: [react()],
  build: {
    rollupOptions: {
      input: {
        main: resolve(__dirname, 'index.html'),
        builder: resolve(__dirname, 'builder.html'),
      },
    },
  },
  server: {
    allowedHosts: true,
    proxy: {
      '/graphql': {
        target: 'http://localhost:9191',
        changeOrigin: true,
      },
      // Document uploads are REST/multipart, not GraphQL: the file is binary and a base64-in-
      // JSON round trip would inflate every upload by a third. Without this entry a dev upload
      // falls through to the SPA and the browser receives index.html instead of JSON - the same
      // failure mode the /graphql candidates in constants.js were built to detect.
      '/api': {
        target: 'http://localhost:9191',
        changeOrigin: true,
        // Uploads are slower than a query; do not let the dev server cut them off.
        timeout: 120000,
      }
    }
  }
});
