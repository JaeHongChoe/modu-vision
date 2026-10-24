import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'path';
import { execFileSync } from 'child_process';
import tailwindcss from 'tailwindcss';
import autoprefixer from 'autoprefixer';

// The bundle's source commit for local records (the first-use recorder's build_sha): HEAD only when the working tree has
// no tracked or untracked change, so it names exactly the code that was built; null otherwise, and without git.
export function cleanCommitSha(run: (args: string[]) => string = (args) => execFileSync('git', args, { cwd: __dirname, encoding: 'utf8', stdio: ['ignore', 'pipe', 'ignore'] })): string | null {
  try {
    const sha = run(['rev-parse', 'HEAD']).trim();
    return /^[0-9a-f]{40}$/.test(sha) && run(['status', '--porcelain']).trim() === '' ? sha : null;
  } catch {
    return null;
  }
}

/** The define value: a production build (`vite build`) only. A dev server keeps the first value while HMR swaps the code
 *  it serves, so there the record says null rather than a commit the running code may no longer match. */
export function buildShaDefine(command: 'build' | 'serve', run?: (args: string[]) => string): string {
  return JSON.stringify(command === 'build' ? cleanCommitSha(run) : null);
}

// https://vitejs.dev/config/
export default defineConfig(({ command }) => ({
  plugins: [react()],
  define: {
    __MODU_BUILD_SHA__: buildShaDefine(command),
  },
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
}));
