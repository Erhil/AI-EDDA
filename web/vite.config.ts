import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';

const target = process.env.AI_EDDA_API_TARGET ?? 'http://127.0.0.1:8000';
export default defineConfig(({ mode }) => ({
  plugins: [react()],
  publicDir: mode === 'demo' ? 'demo-public' : 'public',
  server: mode === 'demo' ? { host: '127.0.0.1', port: 5174, strictPort: true } : { proxy: { '/api': { target, changeOrigin: true, configure(proxy) {
    proxy.on('proxyReq', (request, incoming) => {
      const origin = incoming.headers.origin;
      if (origin && incoming.headers.host) {
        const scheme = incoming.socket.encrypted ? 'https' : 'http';
        if (origin === `${scheme}://${incoming.headers.host}`) {
          request.setHeader('origin', new URL(target).origin);
        }
      }
    });
  } } } },
  build: { outDir: '../src/ai_edda/static', emptyOutDir: true },
}));
