import { defineConfig } from 'vite';
import react from '@vitejs/plugin-react';
import path from 'node:path';

// Builds a single self-contained IIFE bundle that exposes window._mountMuiChart
// and window._unmountMuiChart. Output goes straight into Flask's static folder.
export default defineConfig({
  plugins: [react()],
  define: { 'process.env.NODE_ENV': JSON.stringify('production') },
  build: {
    outDir: path.resolve(__dirname, '../static'),
    emptyOutDir: false,
    sourcemap: false,
    minify: 'esbuild',
    lib: {
      entry: path.resolve(__dirname, 'src/charts.jsx'),
      name: 'LoyaltyGuestsCharts',
      formats: ['iife'],
      fileName: () => 'charts.bundle.js',
    },
  },
});
