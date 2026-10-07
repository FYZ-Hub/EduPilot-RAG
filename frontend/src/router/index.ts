import { createRouter, createWebHistory, type Router, type RouterHistory } from 'vue-router'

import AppLayout from '@/layouts/AppLayout.vue'

/**
 * 路由表固定为 UI_SPEC 2.1：/ -> 首页，另有 /knowledge、/chat、/planning 与 404。
 */
export function createAppRouter(history?: RouterHistory): Router {
  return createRouter({
    history: history ?? createWebHistory(),
    routes: [
      {
        path: '/',
        component: AppLayout,
        children: [
          {
            path: '',
            name: 'home',
            component: () => import('@/views/HomeView.vue'),
            meta: { title: '首页', description: '三项核心能力与快速入口' },
          },
          {
            path: 'knowledge',
            name: 'knowledge',
            component: () => import('@/views/KnowledgeView.vue'),
            meta: { title: '知识库管理', description: '管理检索与规划使用的可信资料' },
          },
          {
            path: 'chat',
            name: 'chat',
            component: () => import('@/views/ChatView.vue'),
            meta: { title: 'RAG 问答', description: '按证据回答问题并展示引用来源' },
          },
          {
            path: 'planning',
            name: 'planning',
            component: () => import('@/views/PlanningView.vue'),
            meta: { title: '学业规划', description: '由确定性规则计算学分缺口' },
          },
        ],
      },
      {
        path: '/:pathMatch(.*)*',
        name: 'not-found',
        component: () => import('@/views/NotFoundView.vue'),
        meta: { title: '页面不存在', description: '未找到请求的页面' },
      },
    ],
  })
}
