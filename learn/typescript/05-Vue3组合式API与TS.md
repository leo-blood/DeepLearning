# 05 Vue 3 组合式 API 与 TypeScript

> 本章以项目 `cs-fe-ess` 的真实 Hook 代码为主线，讲解 Vue 3 + TS 的核心模式。

---

## 1. `ref` 与 `Ref<T>`

```ts
import { ref, Ref } from 'vue';

// TS 从初始值自动推断类型
const count = ref(0);          // Ref<number>
const name  = ref('');         // Ref<string>

// 初始值为 null/undefined 时，需要显式指定
const user  = ref<IUser | null>(null);  // Ref<IUser | null>
```

**使用 `Ref<T>` 作为接口字段类型**（项目中的标准写法）：

```ts
// src/hooks/performance.ts
interface IPerformance {
  loading: Ref<boolean>;
  earnings: Ref<CloudStaffEarningsVO | undefined>;
  salaryDate: Ref<Date | string>;
}
```

好处：接口的消费方知道这些字段是响应式的，需要通过 `.value` 访问。

---

## 2. `computed` 与 `ComputedRef<T>`

```ts
import { computed, ComputedRef } from 'vue';

const showProgress: ComputedRef<boolean> = computed(
  () => activeTab.value !== LearningStatus.FINISHED
);
```

**项目中的完整例子** — `src/hooks/performance.ts`：

```ts
const amount = computed(() => {
  const { totalSalary } = earnings.value || {};
  if (!totalSalary) return '本月无收益';
  if (totalSalary.value == null || isNaN(Number(totalSalary.value))) return totalSalary.value;
  return Number(totalSalary.value).toFixed(2);
});
// amount 类型被推断为 ComputedRef<string | undefined>
```

---

## 3. `reactive` 与对象类型

```ts
import { reactive } from 'vue';

// reactive 对象的类型由初始值推断
const state = reactive({
  name: '',
  age: 0,
});
// state 类型：{ name: string; age: number }
```

**项目中的例子** — `src/hooks/myCourse.ts`：

```ts
const myCourseList = reactive({
  [LearningStatus.IN_PROGRESS]:   [] as MyLearningTaskVo[],
  [LearningStatus.FINISHED]:      [] as MyLearningTaskVo[],
  [LearningStatus.NOT_FINISHED]:  [] as MyLearningTaskVo[],
});
```

`[] as MyLearningTaskVo[]` 是类型断言，告诉 TS 这个空数组的元素类型，否则推断为 `never[]`。

---

## 4. Hook 函数模式（Composable）

Vue 3 的核心最佳实践：用函数封装逻辑，返回响应式状态和操作方法。

**完整结构**：

```ts
// 1. 定义返回值接口（文档 + 约束）
interface IUseCounter {
  count: Ref<number>;
  increment: () => void;
  decrement: () => void;
  reset: () => void;
}

// 2. 实现 Hook 函数
const useCounter = (initial = 0): IUseCounter => {
  const count = ref(initial);

  const increment = () => { count.value++; };
  const decrement = () => { count.value--; };
  const reset     = () => { count.value = initial; };

  return { count, increment, decrement, reset };
};

// 3. 在组件中使用
const { count, increment } = useCounter(10);
```

**项目中的完整 Hook** — `src/hooks/myCourse.ts` 的架构分析：

```ts
// 基础 Hook：封装通用逻辑
const useMyCourse = (): IMyCourse => {
  // 状态声明
  const activeTab = ref(LearningStatus.IN_PROGRESS);
  const loading   = ref(false);
  const myCourseList = reactive({ ... });

  // 计算属性
  const showProgress = computed(() => ...);

  // 副作用（生命周期）
  onMounted(fetchMyCourseList);

  // 暴露的方法
  const fetchMyCourseList = async (): Promise<void> => { ... };
  const goStudy = ({ studyUrl }: MyLearningTaskVo): void => { ... };

  // 返回所有需要暴露的内容
  return { activeTab, loading, showProgress, fetchMyCourseList, goStudy, ... };
};

// 扩展 Hook：继承基础逻辑，增加平台特有功能
export const useMyCourseDesktop = (): IMyCourseDesktop => {
  const myCourse = useMyCourse();             // 复用基础 Hook
  const { searchKeyword, fetchMyCourseList } = myCourse;

  const courseActionFilter = (course: MyLearningTaskVo): string => {
    return course.status === LearningStatus.IN_PROGRESS ? '继续学习' : '回顾';
  };

  watch(searchKeyword, debounce(fetchMyCourseList, 800, { leading: true }));

  return { ...myCourse, courseActionFilter };  // 展开 + 扩展
};
```

**关键设计要点**：
1. `useMyCourse` 私有（不导出），只作为内部复用
2. Desktop / Mobile / Schedule 各自扩展，通过 `...myCourse` 展开共享状态
3. 接口继承 `IMyCourseDesktop extends IMyCourse` 保证类型完整性

---

## 5. `watch` 与类型

```ts
import { watch, WatchStopHandle } from 'vue';

// 监听单个 ref
const stop: WatchStopHandle = watch(loading, (newVal, oldVal) => {
  // newVal: boolean, oldVal: boolean（从 Ref<boolean> 自动推断）
  console.log('loading changed:', newVal);
});

// 监听多个值
watch([loading, activeTab], ([newLoading, newTab]) => {
  // 类型推断为 [boolean, LearningStatus]
});

// deep watch 对象
watch(
  searchHistory,
  async () => { await setStore(LOCAL_STORAGE_KEY, searchHistory.value); },
  { deep: true }
);
```

---

## 6. 生命周期钩子

```ts
import { onMounted, onBeforeMount, onUnmounted } from 'vue';

onMounted(() => {
  // DOM 已挂载
  fetchMyCourseList();
});

onUnmounted(() => {
  // 组件销毁前清理
  stopWatcher();
});
```

**项目中的例子**：

```ts
// src/hooks/myCourse.ts
onMounted(fetchMyCourseList);  // 挂载后立即请求数据

onBeforeMount(() => {
  if (isSearchPage.value) {
    showSearchHistory.value = true;
  }
});
```

---

## 7. 在 `.vue` 文件中使用类型

```vue
<script setup lang="ts">
import { ref, computed } from 'vue'

// defineProps 的类型定义
const props = defineProps<{
  courseId: number
  title: string
  optional?: boolean
}>()

// 带默认值时使用 withDefaults
const propsWithDefaults = withDefaults(defineProps<{
  size?: 'small' | 'medium' | 'large'
}>(), {
  size: 'medium'
})

// defineEmits 类型定义
const emit = defineEmits<{
  (e: 'change', value: string): void
  (e: 'submit'): void
}>()

// 使用 Hook
const { loading, fetchMyCourseList } = useMyCourseDesktop()
</script>
```

---

## 8. 类型守卫（Type Guards）

类型守卫让 TS 在条件分支内自动收窄类型：

```ts
// typeof 守卫
function process(value: string | number) {
  if (typeof value === 'string') {
    value.toUpperCase();  // 这里 value 是 string
  } else {
    value.toFixed(2);     // 这里 value 是 number
  }
}

// instanceof 守卫
function handleError(error: unknown) {
  if (error instanceof Error) {
    console.log(error.message);  // error 是 Error 类型
  }
}

// 自定义类型守卫（is 关键字）
function isCourse(item: unknown): item is MyLearningTaskVo {
  return typeof item === 'object' && item !== null && 'studyUrl' in item;
}
```

---

## 练习

分析项目中 `useMyCourseMobile` 和 `useMyCourseMobileSchedule` 的异同：

1. 两者都有 `isSearchPage`，但类型不同——一个是 `ComputedRef<boolean>`，一个是 `Ref<boolean>`。为什么？
2. `handleSearch` 在 Mobile 版本是 `async`（返回 `Promise<void>`），而在 Schedule 版本是同步的。这对调用方有什么影响？

<details>
<summary>参考答案</summary>

1. Mobile 版本的 `isSearchPage` 是从路由 `route.path` 计算而来的，路由是只读的外部状态，所以用 `computed`（派生状态）。Schedule 版本没有路由，用一个本地 `ref` 手动控制显隐，所以是 `Ref<boolean>`（可写状态）。

2. 调用方如果需要等待搜索完成后再执行某些操作，Mobile 版本可以 `await handleSearch()`；Schedule 版本的 `handleSearch` 返回 `void`，调用方无法感知异步完成时机，需要通过别的机制（如 watch loading）来响应完成事件。

</details>
