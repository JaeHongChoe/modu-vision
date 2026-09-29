import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import tailwindcss from 'tailwindcss';
import autoprefixer from 'autoprefixer';

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  css: {
    postcss: {
      plugins: [tailwindcss(), autoprefixer()],
    },
  },
  base: './', // CRITICAL for Electron local file protocol
  root: path.resolve(__dirname),
  publicDir: 'public',
  build: {
    outDir: path.resolve(__dirname, 'dist'),
    emptyOutDir: true,
    rollupOptions: {
      input: {
        main: path.resolve(__dirname, 'index.html'),
      },
    },
  },
  resolve: {
    alias: {
      '@': path.resolve(__dirname, 'src/renderer'),
    },
  },
  server: {
    port: 5173,
    strictPort: true,
    host: '127.0.0.1',
    // Backend output files are written below the project root. Exporting a
    // report must not reload the renderer and discard the operator's session.
    watch: {
      ignored: [
        '**/annotations/**',
        '**/datasets/**',
        '**/models/**',
        '**/projects/**',
        '**/release/**',
        '**/reports/**',
      ],
    },
  },
});
