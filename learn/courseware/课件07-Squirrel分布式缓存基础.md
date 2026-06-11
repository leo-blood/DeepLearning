# 课件 07｜Squirrel 分布式缓存：Redis 基础与核心操作

---

## 课程目标

学完本课件后，你能够：

1. 解释 Redis 常用数据结构及其适用场景
2. 使用 StoreKey 设计类型安全的缓存键
3. 实现 Cache-Aside（旁路缓存）模式
4. 使用原子操作避免并发竞态问题

---

## 一、Redis 基础回顾

### 1.1 五种核心数据结构

| 类型 | 特点 | 典型场景 |
|------|------|---------|
| **String** | 最简单，支持原子操作 | 缓存对象、计数器、分布式锁 |
| **Hash** | 字段-值映射 | 对象存储（减少序列化开销） |
| **List** | 有序，支持两端操作 | 消息队列、最近操作记录 |
| **Set** | 无序，元素唯一 | 去重、标签集合 |
| **ZSet** | 有序集合，带分数 | 排行榜、按时间排序 |

### 1.2 为什么需要 Squirrel 封装？

```
原始 Redis 问题：

// 不同服务共用 Redis，Key 可能冲突
redisTemplate.set("user:123", "data1");   // 用户服务
redisTemplate.set("user:123", "data2");   // 订单服务（覆盖了！）

// 返回类型不安全
Object value = redisTemplate.get("user:123");  // 什么类型？

// Key 规范全靠人维护，容易出错

Squirrel 解决方案：
  StoreKey → 命名空间 + 类型安全
  Category → 业务隔离
  统一 Key 生成规则
```

---

## 二、StoreKey 设计

### 2.1 StoreKey 概念

```java
// StoreKey = 命名空间 + 键模板 + 值类型
StoreKey<String> STAFF_INFO_KEY = StoreKey.builder()
        .category("ccs_admin")      // 业务分类（命名空间）
        .keyPattern("staff:info:{staffMis}")  // Key 模板
        .valueType(String.class)    // 值类型（类型安全）
        .expireSeconds(3600)        // TTL
        .build();

// 最终 Redis Key：ccs_admin:staff:info:wangwj
String redisKey = STAFF_INFO_KEY.generate("wangwj");
// → "ccs_admin:staff:info:wangwj"
```

### 2.2 统一管理 StoreKey

```java
@Component
public class CcsStoreKeys {

    // 员工信息缓存（1小时）
    public static final StoreKey<StaffInfoDTO> STAFF_INFO =
        StoreKey.<StaffInfoDTO>builder()
            .category("ccs_admin")
            .keyPattern("staff:info:{mis}")
            .valueType(StaffInfoDTO.class)
            .expireSeconds(3600)
            .build();

    // 任务列表缓存（5分钟）
    public static final StoreKey<List<TaskDTO>> TASK_LIST =
        StoreKey.<List<TaskDTO>>builder()
            .category("ccs_admin")
            .keyPattern("task:list:{staffMis}:{date}")
            .valueType(new TypeReference<List<TaskDTO>>() {})
            .expireSeconds(300)
            .build();

    // 请求计数（1天）
    public static final StoreKey<Long> REQUEST_COUNT =
        StoreKey.<Long>builder()
            .category("ccs_admin")
            .keyPattern("request:count:{date}:{type}")
            .valueType(Long.class)
            .expireSeconds(86400)
            .build();
}
```

---

## 三、基础 CRUD 操作

### 3.1 注入 Squirrel Client

```java
@Service
@Slf4j
public class StaffCacheService {

    @Autowired
    private SquirrelClient squirrelClient;  // Squirrel 提供的客户端
}
```

### 3.2 读写操作

```java
// 写入
squirrelClient.set(CcsStoreKeys.STAFF_INFO, "wangwj", staffInfoDTO);

// 读取（返回 Optional）
Optional<StaffInfoDTO> cached = squirrelClient.get(CcsStoreKeys.STAFF_INFO, "wangwj");
StaffInfoDTO info = cached.orElse(null);

// 删除
squirrelClient.delete(CcsStoreKeys.STAFF_INFO, "wangwj");

// 判断是否存在
boolean exists = squirrelClient.exists(CcsStoreKeys.STAFF_INFO, "wangwj");

// 设置过期时间（已存在的 key）
squirrelClient.expire(CcsStoreKeys.STAFF_INFO, "wangwj", 3600);
```

---

## 四、Cache-Aside 模式

### 4.1 模式说明

```
Cache-Aside（旁路缓存）是最常用的缓存模式：

读操作：
  1. 查缓存
  2. 命中 → 直接返回
  3. 未命中 → 查 DB → 写入缓存 → 返回

写操作：
  1. 更新 DB
  2. 删除缓存（而非更新缓存！）
  原因：更新缓存可能造成并发写冲突，删除更安全
```

### 4.2 标准实现

```java
@Service
public class StaffInfoService {

    @Autowired
    private SquirrelClient squirrelClient;

    @Autowired
    private StaffRepository staffRepository;

    /**
     * 读：Cache-Aside
     */
    public StaffInfoDTO getStaffInfo(String staffMis) {
        // 1. 查缓存
        Optional<StaffInfoDTO> cached = squirrelClient.get(CcsStoreKeys.STAFF_INFO, staffMis);
        if (cached.isPresent()) {
            return cached.get();
        }

        // 2. 缓存未命中，查 DB
        StaffInfo staff = staffRepository.findByMis(staffMis);
        if (staff == null) {
            return null;
        }

        StaffInfoDTO dto = toDTO(staff);

        // 3. 写入缓存
        squirrelClient.set(CcsStoreKeys.STAFF_INFO, staffMis, dto);

        return dto;
    }

    /**
     * 写：更新 DB + 删除缓存
     */
    public void updateStaffInfo(String staffMis, UpdateStaffRequest request) {
        // 1. 更新 DB
        staffRepository.update(staffMis, request);

        // 2. 删除缓存（而非更新）
        squirrelClient.delete(CcsStoreKeys.STAFF_INFO, staffMis);
        // 下次读取时会重新从 DB 加载，保证数据一致
    }
}
```

### 4.3 缓存空值（防缓存穿透）

```java
// 问题：查询不存在的数据，每次都打穿 DB
// 解决：缓存空值（缓存 null 标记）

private static final String NULL_VALUE = "__NULL__";

public StaffInfoDTO getWithNullCache(String staffMis) {
    Optional<String> cached = squirrelClient.get(CcsStoreKeys.STAFF_INFO_RAW, staffMis);

    if (cached.isPresent()) {
        if (NULL_VALUE.equals(cached.get())) {
            return null;  // 命中空值缓存，直接返回 null
        }
        return JSON.parseObject(cached.get(), StaffInfoDTO.class);
    }

    StaffInfo staff = staffRepository.findByMis(staffMis);
    if (staff == null) {
        // 缓存空值，TTL 较短（避免长期缓存脏数据）
        squirrelClient.setWithExpire(CcsStoreKeys.STAFF_INFO_RAW, staffMis, NULL_VALUE, 60);
        return null;
    }

    squirrelClient.set(CcsStoreKeys.STAFF_INFO_RAW, staffMis, JSON.toJSONString(toDTO(staff)));
    return toDTO(staff);
}
```

---

## 五、原子操作

### 5.1 计数器操作

```java
// 原子自增（线程安全）
Long newCount = squirrelClient.incrBy(
        CcsStoreKeys.REQUEST_COUNT,
        LocalDate.now().toString(), "API",
        1L
);

// 原子自减
Long remaining = squirrelClient.decrBy(
        CcsStoreKeys.REQUEST_COUNT,
        "quota:" + staffMis,
        1L
);
if (remaining < 0) {
    throw new QuotaExceededException("超出每日配额");
}
```

### 5.2 SET NX（Set if Not eXists）

```java
// SET NX：仅当 key 不存在时才设置（原子操作）
// 场景：初始化，防止覆盖已有数据

boolean success = squirrelClient.setNx(
        CcsStoreKeys.INIT_FLAG,
        "task-init-flag",
        "initialized"
);
if (success) {
    // 我是第一个，执行初始化
    doInitialize();
}
// 如果 success=false，说明已经有人初始化了
```

---

## 六、缓存 Key 设计规范

| 规范 | 示例 | 原因 |
|------|------|------|
| 使用 Category 隔离业务 | `ccs_admin:...` | 避免跨服务 Key 冲突 |
| 使用 `:` 分层 | `staff:info:wangwj` | 支持 Key 扫描和监控 |
| 不使用特殊字符 | 避免空格、`*`、`?` | Redis 命令兼容性 |
| TTL 必须设置 | `expireSeconds(3600)` | 防止 Key 永久积累 |
| 变量部分放末尾 | `staff:info:{mis}` | 便于 Key 扫描 |

---

## 七、核心知识点总结

```
StoreKey 核心价值：
  Category → 命名空间隔离
  keyPattern → 类型化 Key 模板
  valueType → 编译期类型检查
  expireSeconds → 强制设置 TTL

Cache-Aside 模式：
  读：查缓存 → 未命中查DB → 写缓存
  写：更新DB → 删除缓存（不更新）
  空值缓存 → 防穿透

原子操作：
  incrBy/decrBy → 线程安全计数
  setNx → 原子初始化/占位

TTL 选择：
  高频读、低频更新 → 1小时+
  会话数据 → 30分钟
  临时限流数据 → 1-5分钟
  空值缓存 → 1-5分钟（短）
```

---

## 课后练习

1. **设计：** 为一个"员工技能标签"功能设计 StoreKey，需要存储每个员工的技能列表（List），考虑 TTL、Category、Key 结构
2. **实践：** 用 Cache-Aside 模式包装 `taskRepository.findByStaffMis()`，注意处理数据更新时的缓存失效
3. **思考：** 为什么缓存更新时要"删除缓存"而不是"更新缓存"？描述更新缓存可能导致的并发问题

---

*← 上一课：Crane 分布式定时任务 | 下一课：Squirrel 分布式锁与高级问题 →*
