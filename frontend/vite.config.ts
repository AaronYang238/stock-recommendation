import { defineConfig } from 'vite'
import react from '@vitejs/plugin-react'

// 开发端口沿用 9090；/api 代理到 Django(:8000)，避免跨域，生产同源部署亦无需改动。
export default defineConfig({
  plugins: [react()],
  server: {
    host: true,        // 绑定 0.0.0.0，允许外部/局域网访问（默认仅本机）
    port: 9090,
    proxy: {
      // /api 由 Vite 在「服务器侧」代理到 Django，故 Django 只需监听本机即可
      '/api': 'http://localhost:8000',
    },
  },
})
