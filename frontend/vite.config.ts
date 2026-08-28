import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// https://vite.dev/config/
export default defineConfig({
  plugins: [react()],
  // MapLibre ships its own web worker. Pre-bundling it can leave Vite pointing
  // at a stale generated worker after HMR, which presents as a blank map.
  optimizeDeps: {
    exclude: ['maplibre-gl'],
  },
  server: {
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
  build: {
    // Mapping engines are inherently large but highly cacheable. Keep them out
    // of the application chunk so ordinary UI changes do not invalidate them.
    chunkSizeWarningLimit: 1100,
    rollupOptions: {
      output: {
        manualChunks(id) {
          if (id.includes('maplibre-gl')) return 'maplibre-gl';
          if (id.includes('@deck.gl') || id.includes('@luma.gl')) return 'deck-gl';
          if (id.includes('react') || id.includes('@tanstack')) return 'react-vendor';
          return undefined;
        },
      },
    },
  },
})
