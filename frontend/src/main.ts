import { createApp } from 'vue'
import { createPinia } from 'pinia'
import ElementPlus from 'element-plus'
import zhCn from 'element-plus/es/locale/lang/zh-cn'
import 'element-plus/dist/index.css'

import App from './App.vue'
import { createAppRouter } from './router'
import './styles/tokens.css'
import './styles/global.css'

const app = createApp(App)

app.use(createPinia())
app.use(createAppRouter())
app.use(ElementPlus, { locale: zhCn })

app.mount('#app')
