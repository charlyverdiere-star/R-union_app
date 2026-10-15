import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// En développement, le serveur Vite relaie les appels /api vers le backend
// Python : l'interface et l'API partagent donc la même origine, ce qui évite
// toute configuration CORS et préserve l'accès au microphone.
export default defineConfig({
  plugins: [react()],
  server: {
    port: 5173,
    proxy: {
      '/api': { target: 'http://127.0.0.1:8420', changeOrigin: true },
    },
  },
  build: { outDir: 'dist', sourcemap: false, chunkSizeWarningLimit: 900 },
})
