import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 开发端口沿用 9090；/api 代理到 Django(:8000)，避免跨域，生产同源部署亦无需改动。
export default defineConfig({
  plugins: [react()],
  server: {
    port: 9090,
    proxy: {
      '/api': 'http://localhost:8000',
    },
  },
})
