import { defineConfig } from 'vite'
import vue from '@vitejs/plugin-vue'
export default defineConfig({
  plugins: [vue()],
  server: {
    proxy: {
      '/api': {
        target: 'http://127.0.0.1:8000',
        // 保留浏览器访问的 Host，让后端的 Origin / Host 同源校验正常工作。
        changeOrigin: false,
      },
    },
  },
})
