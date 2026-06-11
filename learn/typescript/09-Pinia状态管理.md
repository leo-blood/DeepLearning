# 09 Pinia 状态管理

> Pinia 是 Vue 3 官方推荐的状态管理库，用于在多个组件间共享状态。

---

## 1. 为什么需要状态管理

组件自己的 `ref` / `reactive` 是本地状态，刷新或切换路由就丢失。
有些数据需要**全局共享**（用户信息、权限菜单、loading 状态），这就是 Pinia 的用武之地：

```
App.vue  ──── 直接读 store.userInfo
  ├── Header.vue  ──── 直接读 store.userInfo
  ├── Sidebar.vue ──── 直接读 store.menus
  └── Page.vue    ──── 调用 store.getUserInfo()
```

---

## 2. 创建 Pinia 实例

**项目中的 `src/store/index.ts`**：

```ts
import { createPinia } from 'pinia'
import piniaPersist from 'pinia-plugin-persist'  // 持久化插件

const pinia = createPinia()
pinia.use(piniaPersist)   // 注册插件

export default pinia
```

在 `main.ts` 中注册到 Vue 应用：

```ts
import { createApp } from 'vue'
import App from './App.vue'
import pinia from './store'

createApp(App).use(pinia).mount('#app')
```

---

## 3. Options Store（项目使用的写法）

**项目 `src/store/businessInfo.ts` — 最简洁的 store**：

```ts
import { defineStore } from 'pinia'
import { getRankGetStaffBusinessInfo } from '@/api/types/Schedule/api'

// 定义 state 类型
type InitState = {
  businessInfo: StaffBusinessResponseVO[]
}

export default defineStore('businessInfoStore', {
  // state：响应式数据（必须是函数，返回初始值）
  state: (): InitState => ({
    businessInfo: [],
  }),

  // actions：修改 state 的方法（可以是异步的）
  actions: {
    async getStaffBusinessInfo() {
      const { data } = await getRankGetStaffBusinessInfo()

      // 排序：主业务排前面
      data?.sort((a: StaffBusinessResponseVO, b: StaffBusinessResponseVO) => {
        if (a.isMainBusiness && !b.isMainBusiness) return -1
        if (!a.isMainBusiness && b.isMainBusiness) return 1
        return 0
      })

      this.businessInfo = data || []  // 直接修改 this，无需 mutations
    },
  },
})
```

三个核心概念对比：

| 概念 | 作用 | Vuex 中的对应物 |
|------|------|--------------|
| `state` | 响应式数据 | state |
| `actions` | 修改数据的方法（同步/异步均可） | mutations + actions |
| `getters` | 计算派生数据（有缓存） | getters |

**Pinia vs Vuex 的最大区别**：Pinia 没有 mutations，直接在 action 中 `this.xxx = value` 即可。

---

## 4. 完整的 Store — userInfoStore

**项目 `src/store/userInfo.ts`** 完整分析：

```ts
import { defineStore } from 'pinia'

type InitState = {
  userInfo: CloudAccountInfoVo | null    // 用户基本信息
  userStatus: string                     // 'pcl'(平台灵活) | 'cl'(云服务商)
  menus: User.IMenu[]                    // 导航菜单（根据身份过滤）
  mobile: string | undefined
  loading: boolean                       // 全局 loading 状态
  errorMessage: string | undefined       // 全局错误信息
  barHeight: number                      // 状态栏高度（APP 内）
  // ...更多字段
}

export default defineStore('userInfoStore', {
  state: (): InitState => ({
    userInfo: null,
    userStatus: '',
    menus: [],
    mobile: '',
    loading: true,    // 初始 true，数据加载完才变 false
    errorMessage: '',
    barHeight: 0,
    // ...
  }),

  actions: {
    // 核心 action：获取用户信息
    async getUserInfo() {
      try {
        const res = await getCloudAccountGet()
        const { data = null, message } = res

        if (data) {
          // 解析用户身份：mis 前缀是 'cl_xxx' 则是云服务商，否则是平台灵活
          const misPrefix = data.mis ? data.mis.split('_') : 'pcl'
          this.userStatus = misPrefix.length > 1 ? misPrefix[0] : 'pcl'

          this.userInfo = { ...data }
          this.mobile   = data.mobile

          // 根据身份过滤菜单
          this.menus = (menus as User.IMenu[]).filter(
            (menu: User.IMenu) =>
              (!menu.auth || menu.auth === this.userStatus) &&
              ((isPC && !!menu.pc) || (!isPC && !!menu.app))
          )

          // 根据身份触发不同的子请求
          if (this.userStatus === 'pcl' && this.userInfo.mis) {
            this.getStudyTask(this.userInfo.mis)
          }
          if (this.userStatus === 'cl' && this.workInfo?.length) {
            this.getEssGetRecallId()
          }

          this.errorMessage = ''
        } else {
          this.errorMessage = message
        }
      } catch (error: unknown) {
        this.errorMessage = (error as ICustomError).message
      }

      this.loading = false  // 无论成功失败，都结束 loading
    },

    // 简单的同步 action
    updateMobile(mobile: string) {
      this.mobile = mobile
    },

    updateBarHeight(height: number) {
      this.barHeight = height
    },

    // store 内部也可以调用另一个 action
    async getStudyTask(pclMis: string) {
      const res = await postStudentTaskGetContentCollectionByPcl({ pclMis })
      this.pclCourseInfo = res.data
    },
  },
})
```

---

## 5. 在组件中使用 Store

### 5.1 基本读写

```vue
<script setup lang="ts">
import userInfoStore from '@/store/userInfo'

// 调用 store 函数，返回 store 实例
const store = userInfoStore()

// 读取 state（自动响应式）
const name = store.userInfo?.name

// 调用 action
store.getUserInfo()

// 直接修改 state（Pinia 允许，但 action 更规范）
store.loading = false
</script>

<template>
  <div v-if="store.loading">加载中...</div>
  <div v-else>{{ store.userInfo?.name }}</div>
</template>
```

### 5.2 解构（注意响应式陷阱）

```ts
import { storeToRefs } from 'pinia'
import userInfoStore from '@/store/userInfo'

const store = userInfoStore()

// ❌ 直接解构：失去响应式
const { loading, userInfo } = store

// ✅ storeToRefs：保留响应式
const { loading, userInfo } = storeToRefs(store)
// loading 和 userInfo 现在是 Ref<T>，访问要用 .value

// ✅ actions 可以直接解构（函数不需要响应式）
const { getUserInfo, updateMobile } = store
```

### 5.3 项目中的 Class 组件写法（遗留代码）

```ts
// src/views/app/App.vue
export default class Home extends Vue {
  // 在 class 中使用 store
  userStore = userInfoStore()

  errorMessage = computed(() => {
    return this.userStore.errorMessage
  })

  loading = computed(() => {
    return this.userStore.loading
  })

  onAuthorized = (ssoid: string) => {
    if (ssoid) {
      this.userStore.getUserInfo()  // 调用 action
    }
  }
}
```

---

## 6. Store 间相互调用

**项目中的 `src/store/log.ts`** — 在 logStore 中访问 userInfoStore：

```ts
import { defineStore } from 'pinia'
import userInfoStore from './userInfo'  // 导入另一个 store

export default defineStore('logStore', {
  state: (): InitState => ({
    pageCase: undefined,
  }),
  actions: {
    setPageCase(cid: string) {
      // 在 action 中调用另一个 store（直接调用，获取实例）
      const { userInfo } = userInfoStore()
      this.pageCase = Lx(LX[LX_TYPE].CID[cid.toUpperCase()], {
        ykf_mis: userInfo?.mis || '',
      })
    },

    getValLab() {
      const { userInfo } = userInfoStore()  // 每次调用获取最新数据
      return {
        custom: { ykf_mis: userInfo?.mis || '' },
      }
    },
  },
})
```

**关键点**：在 action 中调用其他 store 时，直接在函数内部调用 `otherStore()` 即可，不要在 store 外部持有引用（避免循环依赖初始化问题）。

---

## 7. Getters（计算派生状态）

```ts
defineStore('courseStore', {
  state: () => ({
    courses: [] as MyLearningTaskVo[],
    searchKeyword: '',
  }),

  // getters 类似 computed，有缓存
  getters: {
    filteredCourses(state): MyLearningTaskVo[] {
      if (!state.searchKeyword) return state.courses
      return state.courses.filter(c =>
        c.name?.includes(state.searchKeyword)
      )
    },

    courseCount(state): number {
      return state.courses.length
    },

    // getter 调用另一个 getter
    hasNoResults(): boolean {
      return this.filteredCourses.length === 0
    },
  },
})
```

---

## 8. 持久化（pinia-plugin-persist）

项目已注册了 `pinia-plugin-persist` 插件，在 store 定义中启用持久化：

```ts
defineStore('settingsStore', {
  state: () => ({
    theme: 'light',
    language: 'zh-CN',
  }),

  // persist 配置持久化
  persist: {
    enabled: true,           // 开启持久化
    strategies: [
      {
        storage: localStorage,   // 存到 localStorage（默认）
        paths: ['theme'],        // 只持久化 theme 字段
      },
    ],
  },
})
```

---

## 9. Setup Store（组合式写法，可选）

除了 Options 写法，Pinia 也支持 Composition API 风格：

```ts
import { defineStore } from 'pinia'
import { ref, computed } from 'vue'

// 与 <script setup> 语法完全一致
export const useCounterStore = defineStore('counter', () => {
  // ref → state
  const count = ref(0)

  // computed → getter
  const double = computed(() => count.value * 2)

  // 普通函数 → action
  function increment() {
    count.value++
  }

  return { count, double, increment }
})
```

两种写法功能完全相同，项目目前使用 Options 写法，都应该能读懂。

---

## 练习

分析 `userInfoStore` 的设计，回答以下问题：

1. `loading` 初始值为 `true`，而不是 `false`，为什么？
2. `getUserInfo` action 在 `try/catch` 中请求数据，但 `this.loading = false` 写在 `try/catch` 外面（相当于 `finally`），为什么这样设计比写在 `try` 里面更好？
3. 如果想在多个组件中都能响应式地读到 `userInfo?.name`，应该用 `store.userInfo?.name` 还是解构出来？

<details>
<summary>参考答案</summary>

1. 初始 `true` 意味着"还没开始加载，先显示 loading"。如果初始为 `false`，在请求发出前会短暂显示内容，造成视觉闪烁。

2. `this.loading = false` 写在 `try/catch` 外面（即 `finally` 等效位置）保证无论请求成功还是失败，loading 状态都会被正确清除，不会"永远转圈"。如果只在 `try` 里设置，请求失败时 loading 不会关闭，UI 就卡死了。

3. 在模板里直接用 `store.userInfo?.name` 即可（store 本身是响应式的）。在 `<script setup>` 里如果需要解构出来单独使用，必须用 `storeToRefs`：

```ts
const { userInfo } = storeToRefs(store)
// userInfo 是 Ref<CloudAccountInfoVo | null>
// 模板里用 userInfo?.name，<script> 里用 userInfo.value?.name
```

</details>
