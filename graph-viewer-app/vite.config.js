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
    proxy: {
      '/graphql': {
        target: 'http://localhost:9191',
        changeOrigin: true,
      }
    }
  }
});
