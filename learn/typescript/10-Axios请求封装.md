# 10 Axios 请求封装

> 以 `cs-fe-ess` 项目的 `src/api/interceptor.ts` 和 `src/lib/axiosExtend.ts` 为主线，
> 深入讲解企业级 Axios 封装思路。

---

## 1. Axios 基础

```ts
import axios from 'axios'

// 最简单的请求
const res = await axios.get('https://api.example.com/users')
const data = res.data

// 带参数
const res = await axios.get('/api/users', { params: { page: 1, size: 10 } })

// POST 请求
const res = await axios.post('/api/login', { username: 'test', password: '123' })
```

---

## 2. 创建实例（`axios.create`）

直接用 `axios` 全局对象不灵活，一般创建独立实例：

```ts
const instance = axios.create({
  baseURL: 'https://api.example.com',  // 基础 URL，所有请求都会拼在前面
  timeout: 5000,                        // 超时时间（ms）
  headers: {
    'Content-Type': 'application/json',
  },
})
```

**项目中的做法** — `src/api/interceptor.ts`：

```ts
const axios = Axios.create({
  timeout: 5000,
  // withCredentials: true,  // 跨域带 cookie（按需开启）
})
```

---

## 3. 拦截器（Interceptors）

拦截器是 Axios 最核心的能力，在请求发出前/收到响应后做统一处理。

### 3.1 请求拦截器

```
发起请求  →  [请求拦截器]  →  服务器
```

**项目中的请求拦截器**：

```ts
axios.interceptors.request.use(
  (request) => {
    // ✅ 成功处理：在请求头注入标识
    request.headers['X-Requested-With'] = 'XMLHttpRequest'

    // 泳道路由（用于测试环境流量隔离）
    if (process.env.ESS_SWIMLANE) {
      request.headers.Swimlane = process.env.ESS_SWIMLANE
    }
    
    return request  // 必须返回 request，否则请求不会发出
  },
  (error) => {
    // ❌ 错误处理：请求配置本身出错（极少见）
    if (error.code === 'ECONNABORTED' && error.message.includes('timeout')) {
      return Promise.reject(new Error('请求超时'))
    }
    if (Axios.isCancel(error)) {
      return Promise.reject(new Error('请求被取消'))
    }
    return Promise.reject(error)
  }
)
```

### 3.2 响应拦截器

```
服务器  →  [响应拦截器]  →  你的代码
```

**项目中的多层响应拦截器**（按注册顺序执行）：

```ts
// 第一层：处理特殊路径（收益接口）
axios.interceptors.response.use((res) => {
  if (res.config.url?.startsWith('/api/earnings')) {
    res.data.status = 0    // 强制置为成功状态
    return res
  }
  return res
})

// 第二层：统一处理业务状态码
axios.interceptors.response.use(
  (res) => {
    if (!res?.data || !isObject(res.data)) {
      return Promise.reject(res)  // 非标准格式，拒绝
    }

    if (res.data.code === 200) return res  // 标准成功

    // 业务成功状态码（历史遗留多种）
    // 0成功 -2系统异常 -4参数异常 -7部分操作失败 -8全部操作失败
    if ([-7, -8, 0, -4, -2].includes(res.data.status)) return res

    if (res.data.status === 401) {
      goToLoginPage()  // 未登录，跳转登录页
      return Promise.reject(res.data)
    }

    LogHandleSDK.addBizError(res)  // 上报业务错误
    return Promise.reject(res.data)
  },
  (error) => {
    // 网络错误（非 2xx 状态码）
    if (Axios.isCancel(error)) {
      return new Promise(() => undefined)  // 取消请求，静默处理
    }
    LogHandleSDK.axiosNetworkErrorInterceptors(error)  // 上报网络错误
    return Promise.reject(error)
  }
)
```

**拦截器执行流程**：

```
请求拦截器 1 → 请求拦截器 2 → 发请求 → 响应拦截器 1 → 响应拦截器 2 → 你的代码
```

---

## 4. 多服务 baseURL 管理

项目对接多个后端服务，每个服务有不同的 baseURL：

```ts
// src/api/interceptor.ts

// 用于"继承"拦截器的工具函数
import axiosExtend from '@/lib/axiosExtend'

// 基础实例（附带所有拦截器）
const axios = Axios.create({ timeout: 5000 })
// ...注册所有拦截器...

// 每个服务派生自基础实例，继承所有拦截器
export const userAxios = axiosExtend(axios, {
  baseURL: envConfig.apiOrigin.user,         // https://user.api.meituan.com
})

export const trainingAxios = axiosExtend(axios, {
  baseURL: envConfig.apiOrigin.training,     // https://training.api.meituan.com
})

export const scheduleAxios = axiosExtend(axios, {
  baseURL: envConfig.apiOrigin.schedule,
})

export const performanceAxios = axiosExtend(axios, {
  baseURL: envConfig.apiOrigin.performance,
})
```

**`axiosExtend` 实现原理** — `src/lib/axiosExtend.ts`：

```ts
export default function requestFn(
  axiosInstance: AxiosInstance,
  config?: AxiosRequestConfig
): AxiosInstance {
  // 1. 合并配置（新 baseURL 覆盖旧的）
  const newAxiosInstance = Axios.create({
    ...axiosInstance.defaults,
    ...(config || {}),
  })

  // 2. 复制所有请求拦截器
  axiosInstance.interceptors.request.handlers.forEach((handler) => {
    newAxiosInstance.interceptors.request.use(handler.fulfilled, handler.rejected)
  })

  // 3. 复制所有响应拦截器
  axiosInstance.interceptors.response.handlers.forEach((handler) => {
    newAxiosInstance.interceptors.response.use(handler.fulfilled, handler.rejected)
  })

  return newAxiosInstance
}
```

这样每个服务实例都有：
- ✅ 自己的 baseURL
- ✅ 完整的鉴权、错误处理拦截器

---

## 5. 特殊场景：Blob 下载

普通 Axios 实例会尝试解析 JSON，下载文件需要专用实例：

**项目中的例子** — `src/api/interceptor.ts`：

```ts
// Blob 专用实例：responseType: 'blob' 跳过 JSON 解析
export const performanceBlobAxios = Axios.create({
  baseURL: envConfig.apiOrigin.performance,
  responseType: 'blob',    // 响应数据作为二进制处理
  timeout: 30000,          // 文件较大，超时时间更长
})

// 只复制请求拦截器（鉴权），不复制响应拦截器（跳过 JSON 校验）
axios.interceptors.request.handlers.forEach((handler) => {
  performanceBlobAxios.interceptors.request.use(handler.fulfilled, handler.rejected)
})

// 响应拦截器：只处理取消，其他情况直接放行
performanceBlobAxios.interceptors.response.use(
  (res) => res,
  (error) => {
    if (Axios.isCancel(error)) return new Promise(() => undefined)
    return Promise.reject(error)
  }
)
```

**文件下载完整流程** — `src/hooks/performance.ts`：

```ts
export const exportEarnings = async ({ billMonth }: { billMonth: string }): Promise<void> => {
  // 1. 请求，得到 Blob 数据
  const res = await performanceBlobAxios.get('/export', { params: { billMonth } })
  const blob: Blob = res.data

  // 2. 从响应头解析文件名
  const disposition: string = res.headers['content-disposition'] ?? ''
  const filenameMatch = disposition.match(/filename\*?=(?:UTF-8'')?["']?([^"';\r\n]+)/i)
  const filename = filenameMatch
    ? decodeURIComponent(filenameMatch[1])
    : `earnings_${billMonth}.xlsx`

  // 3. 创建临时下载链接
  const url = URL.createObjectURL(blob)
  const a = document.createElement('a')
  a.href = url
  a.download = filename
  a.click()

  // 4. 释放内存
  URL.revokeObjectURL(url)
}
```

---

## 6. TypeScript + Axios 泛型

Axios 的方法都是泛型的，正确使用可以得到完整类型提示：

```ts
import axios, { AxiosResponse } from 'axios'

// 标准 API 响应格式（项目约定）
interface ApiResponse<T> {
  code: number
  status: number
  message: string
  data: T
}

// 泛型请求封装
async function get<T>(url: string): Promise<T> {
  const res: AxiosResponse<ApiResponse<T>> = await axios.get(url)
  return res.data.data   // 只返回业务数据
}

// 使用：data 类型自动推断为 UserInfo
const user = await get<UserInfo>('/api/user/profile')
user.name   // ✅ 有类型提示
```

**项目的 API 函数结构**（自动生成）：

```ts
// src/api/types/Learning/api.ts（示意）
export async function postStudentCenterGetMyLearningTask(
  params: { taskName?: string }
) {
  return learningAxios.post<ApiResponse<{
    processingLearningTaskList: MyLearningTaskVo[]
    completeLearningTaskList: MyLearningTaskVo[]
    unfinishLearningTaskList: MyLearningTaskVo[]
  }>>('/student-center/getMyLearningTask', params)
}

// 调用时：data 类型自动推断
const { data } = await postStudentCenterGetMyLearningTask({})
data?.processingLearningTaskList  // MyLearningTaskVo[]
```

---

## 7. 错误处理

### 7.1 拦截器统一处理（推荐）

项目在拦截器中已处理了：
- 401 未登录 → 跳转登录页
- 网络错误 → 上报日志
- 业务错误 → reject，让调用方 catch

### 7.2 调用层处理

```ts
// 方式一：try/catch
try {
  const { data } = await getCloudAccountGet()
  this.userInfo = data
} catch (error: unknown) {
  // 拦截器 reject 的错误会到这里
  this.errorMessage = (error as ICustomError).message
}

// 方式二：Promise.catch
getCloudAccountGet()
  .then(({ data }) => { this.userInfo = data })
  .catch((error) => { this.errorMessage = error.message })
```

### 7.3 判断错误类型

```ts
import axios from 'axios'

try {
  await axios.get('/api/data')
} catch (error) {
  if (axios.isAxiosError(error)) {
    // Axios 错误（网络错误、非 2xx 状态码）
    console.log(error.response?.status)  // HTTP 状态码
    console.log(error.response?.data)    // 响应数据
    console.log(error.config.url)        // 请求 URL
  } else {
    // 非 Axios 错误（业务逻辑错误等）
    console.log(error)
  }
}
```

---

## 8. 取消请求（AbortController）

防止组件卸载后还在处理过期请求：

```ts
import { onUnmounted } from 'vue'

const controller = new AbortController()

onUnmounted(() => {
  controller.abort()  // 组件卸载时取消所有进行中的请求
})

// 请求时传入 signal
const res = await axios.get('/api/data', {
  signal: controller.signal,
})
```

---

## 9. 请求并发控制

```ts
// 并发请求（Promise.all）
const [userRes, courseRes] = await Promise.all([
  getCloudAccountGet(),
  postStudentCenterGetMyLearningTask({}),
])

// 竞争请求（Promise.race）— 取最快的那个
const res = await Promise.race([
  axios.get('/api/fast-server/data'),
  axios.get('/api/backup-server/data'),
])
```

---

## 练习

阅读项目 `src/api/interceptor.ts` 中的多层拦截器，回答：

1. 项目注册了多个响应拦截器，第一个处理 `/api/earnings` 路径，第二个做通用校验。如果一个请求 `url` 是 `/api/earnings/list`，两个拦截器都会执行吗？执行顺序是？

2. 为什么 `performanceBlobAxios` 不复制响应拦截器，只复制请求拦截器？如果把响应拦截器也复制过去会发生什么？

3. 项目对取消请求的处理是 `return new Promise(() => undefined)`，而不是 `return Promise.reject(error)`。这两种处理方式对调用方有什么不同影响？

<details>
<summary>参考答案</summary>

1. 两个拦截器都会执行。Axios 响应拦截器按**注册顺序倒序**执行（先注册的后执行），所以顺序是：先进入通用校验拦截器，再进入 earnings 专用拦截器。等等——实际上 Axios 响应拦截器是按注册顺序**正序**执行的（与请求拦截器相反）。因此这里先执行 earnings 专用拦截器，再执行通用校验拦截器。earnings 拦截器把 `status` 强制置 0，通用拦截器看到 `status === 0` 就放行。

2. `performanceBlobAxios` 的 `responseType: 'blob'`，响应数据是二进制流，不是 JSON 对象。如果复制了通用响应拦截器，那个拦截器会检查 `res.data.code` 和 `res.data.status`，但 `res.data` 是 Blob 而非对象，`isObject` 检查失败，直接 reject，文件永远无法下载。

3. `new Promise(() => undefined)` 是一个**永远不 resolve 也不 reject 的 Promise**（pending 状态），调用方的 `await` 会永远挂起，catch 也不会触发——相当于静默忽略取消操作，UI 不会显示任何错误。而 `Promise.reject(error)` 会让调用方的 catch 捕获到错误，如果没有处理可能会弹出错误提示。对于"用户主动取消"的场景，静默处理更符合预期。

</details>
