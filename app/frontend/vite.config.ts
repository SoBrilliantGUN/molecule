import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react-swc';
import path from 'path';

// https://vitejs.dev/config/
export default defineConfig({
  plugins: [react()],
  resolve: {
    alias: {
      '@': path.resolve(__dirname, './src'),
    },
  },
  server: {
    host: '0.0.0.0',
    port: parseInt(process.env.VITE_PORT || '3000'),
    proxy: {
      '/api': {
        target: `http://localhost:${process.env.BACKEND_PORT || '8000'}`,
        changeOrigin: true,
      },
    },
  },
  build: {
    // 不手写 manualChunks：手拆 react/react-dom 与 radix 到不同 chunk 会偶发把
    // scheduler/react 打两份，触发 "n is not a function"。交给 Vite 自动分包。
    sourcemap: true,
    chunkSizeWarningLimit: 1000,
  },
});
