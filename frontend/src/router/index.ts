import { createRouter, createWebHistory } from 'vue-router'

import Overview from '@/views/Overview.vue'
import IntakeView from '@/views/IntakeView.vue'
import WorkbenchView from '@/views/WorkbenchView.vue'

const router = createRouter({
  history: createWebHistory(),
  routes: [
    { path: '/', name: 'overview', component: Overview },
    { path: '/intake', name: 'intake', component: IntakeView },
    { path: '/:moduleKey', name: 'module', component: WorkbenchView, props: true },
  ],
})

export default router
