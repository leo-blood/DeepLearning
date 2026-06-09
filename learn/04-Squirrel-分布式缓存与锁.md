# Squirrel — 美团内部分布式缓存与锁

> 对标开源：Redisson / Jedis
> 底层：Redis
> 项目封装：`SquirrelOpService.java`
> 项目中应用：缓存数据、分布式锁防并发

---

## 第一节：什么是 Squirrel，为什么需要它

### 1.1 直接用 Redis 的痛点

如果项目直接用 Redis 客户端（如 Jedis），需要：

```java
// 原始 Redis 操作（繁琐且危险）
Jedis jedis = jedisPool.getResource();   // 手动管理连接
try {
    jedis.setex("key", 60, "value");     // key 命名混乱，容易冲突
} finally {
    jedis.close();                        // 忘了关会连接泄漏
}
```

问题：
- 连接管理复杂
- key 命名容易跨业务冲突（A 业务的 `userId:123` 和 B 业务的 `userId:123`）
- 序列化/反序列化需要手写
- 集群/分片逻辑需要自处理

### 1.2 Squirrel 解决了什么

```
Squirrel = Redis 连接池管理 + Category 命名隔离 + 序列化封装 + 集群支持

核心抽象：StoreKey(category, key)

category（类别）：业务命名空间，隔离不同业务的 key
key：业务主键
```

两个业务用同一个 Redis 实例，key 不会冲突：

```java
// 业务A
new StoreKey("cratos-incentive", "task:123")   → 实际 key: cratos-incentive:task:123

// 业务B
new StoreKey("cratos-supplement", "task:123") → 实际 key: cratos-supplement:task:123
```

### 1.3 Squirrel 与 @MdpConfig 的联动

本项目把 category 也做成了动态配置：

```java
// SquirrelOpService.java
@MdpConfig("REDIS_CATEGORY:cratos-supplement")  // 默认 cratos-supplement
private String redisCategory;

// 所有 StoreKey 都用这个 category，修改 Lion 配置即可全局生效
StoreKey storeKey = new StoreKey(redisCategory, key);
```

---

## 第二节：Squirrel 核心 API

### 2.1 配置 Redis 连接（squirrel.properties）

```properties
# squirrel.properties（prod 环境）
# 配置名为 cscRedis 的 Redis 连接
squirrel.store.redis.cscRedis.servers=redis-host:6379
squirrel.store.redis.cscRedis.password=$KMS{redisPassword}
squirrel.store.redis.cscRedis.pool.maxActive=20
```

Spring Bean 注入：

```java
@Autowired
@Qualifier("cscRedis")           // 对应 squirrel.properties 中的名称
private RedisStoreClient storeClient;
```

### 2.2 基本操作一览

```java
// 所有操作都基于 StoreKey
StoreKey key = new StoreKey(redisCategory, "user:123");

// SET（带过期时间）
storeClient.set(key, "value", 60);   // 60秒过期

// GET
String value = storeClient.get(key);

// DELETE
storeClient.delete(key);

// SETNX（不存在时才设置）
boolean success = storeClient.setnx(key, "value", 60);
// success=true：设置成功（之前不存在）
// success=false：设置失败（已存在）

// INCRBY（原子加）
Long result = storeClient.incrBy(key, 1L, 60);

// DECRBY（原子减）
Long result = storeClient.decrBy(key, 1L, 60);
```

### 2.3 项目封装的 SquirrelOpService

```java
// SquirrelOpService.java（项目对 Squirrel 的二次封装）
@Service
@Slf4j
public class SquirrelOpService {

    @Autowired
    @Qualifier("cscRedis")
    private RedisStoreClient storeClient;

    @MdpConfig("REDIS_CATEGORY:cratos-supplement")
    private String redisCategory;

    // 读
    public <T> T getString(String key) throws StoreException {
        StoreKey storeKey = new StoreKey(redisCategory, key);
        return storeClient.get(storeKey);
    }

    // 写（带过期时间）
    public Boolean setString(String key, Object value, int expireSecond) throws StoreException {
        StoreKey storeKey = new StoreKey(redisCategory, key);
        return storeClient.set(storeKey, value, expireSecond);
    }

    // 删除
    public Boolean delString(String key) throws StoreException {
        StoreKey storeKey = new StoreKey(redisCategory, key);
        return storeClient.delete(storeKey);
    }

    // 不存在时设置（分布式锁核心）
    public Boolean setNtxString(String key, Object value, int expireSecond) {
        try {
            StoreKey storeKey = new StoreKey(redisCategory, key);
            return storeClient.setnx(storeKey, value, expireSecond);
        } catch (Exception e) {
            log.error("setNtxString error, key:{}", key, e);
            return false;
        }
    }

    // 带过期时间的原子加
    public Long incrBy(String key, long amount, int expireSecond) { ... }

    // 带过期时间的原子减
    public Long decrBy(String key, long amount, int expireSecond) { ... }
}
```

---

## 第三节：分布式锁实现详解

### 3.1 为什么需要分布式锁

**场景**：云办公申请，同一个员工可能同时点击多次"申请"：

```
用户点击申请
    │
    ├─── 请求1 ──▶ 机器A：查询余额 → 余额足够 → 准备扣减
    │                          ↑ 此时机器B也在查
    └─── 请求2 ──▶ 机器B：查询余额 → 余额足够 → 准备扣减
                               ↓
                          两个请求都通过，重复申请！
```

单机锁（`synchronized`）只能锁本机，跨机器无效。

### 3.2 用 SETNX 实现分布式锁

**SETNX 原理**（SET if Not eXists）：

```
Redis 是单线程的，SETNX 是原子操作：
  如果 key 不存在 → 设置成功，返回 true（获锁成功）
  如果 key 已存在 → 设置失败，返回 false（获锁失败，有人在处理）
```

### 3.3 本项目的锁实现

```java
// CloudOfficeStaffApplyService.java
private String staffIdLockFormat = "staff_id_lock-%s";    // 锁 key 模板
private int lockExpireInSeconds = 60 * 30;                 // 锁30分钟自动释放

public void createApply(CloudOfficeStaffApplyRequestVO request) {
    StaffDTO staffDTO = getStaffInfo(request);

    // ① 构造锁 key（以员工 ID 为维度，不同员工互不影响）
    String lockKey = String.format(staffIdLockFormat, staffDTO.getId());

    // ② 尝试获锁（SETNX）
    boolean locked = squirrelOpService.setNtxString(
        lockKey,
        staffDTO.getId(),    // value 存员工ID（调试用）
        lockExpireInSeconds
    );

    if (!locked) {
        // ③ 获锁失败 → 说明同一员工已有请求在处理
        log.info("员工{}申请处理中，拒绝重复提交", staffDTO.getId());
        throw new BusiException("申请处理中，请勿重复提交");
    }

    try {
        // ④ 获锁成功，执行业务逻辑
        doCreateApply(staffDTO, request);
    } finally {
        // ⑤ 无论成功失败，释放锁（用 finally 保证一定执行）
        squirrelOpService.delString(lockKey);
    }
}
```

### 3.4 锁设计的关键点

**关键点1：过期时间防死锁**

```java
// ✅ 必须设置过期时间！
storeClient.setnx(key, value, 60 * 30);  // 30分钟自动释放

// ❌ 不设置过期时间：如果程序崩溃，锁永远不释放 → 死锁！
storeClient.setnx(key, value);
```

**关键点2：finally 释放锁**

```java
// ✅ 用 finally 保证锁一定被释放
try {
    // 业务逻辑
} finally {
    squirrelOpService.delString(lockKey);
}

// ❌ 没有 finally：业务异常时锁不释放，等过期才能重试
try {
    // 业务逻辑
} catch (Exception e) {
    throw e;
    // 锁没释放！要等 30 分钟后自动过期
}
```

**关键点3：锁 key 粒度要精准**

```java
// ✅ 以员工ID为粒度，不同员工并发不影响
String lockKey = String.format("staff_id_lock-%s", staffDTO.getId());

// ❌ 太粗，锁住所有申请，串行化了
String lockKey = "cloud_office_apply_lock";

// ❌ 太细，用时间戳，每次都是新锁，完全没用
String lockKey = "staff_lock_" + System.currentTimeMillis();
```

---

## 第四节：缓存使用场景

### 4.1 缓存读取模式（Cache-Aside）

项目中虽然没有大量缓存使用，但 Squirrel 支持标准的旁路缓存模式：

```java
public StaffInfo getStaffInfo(String mis) {
    String cacheKey = "staff:" + mis;

    // ① 先查缓存
    StaffInfo cached = squirrelOpService.getString(cacheKey);
    if (cached != null) {
        return cached;                    // 缓存命中，直接返回
    }

    // ② 缓存未命中，查数据库
    StaffInfo staffInfo = staffMapper.queryByMis(mis);

    // ③ 写入缓存，设置过期时间
    squirrelOpService.setString(cacheKey, staffInfo, 300);  // 5分钟缓存

    return staffInfo;
}
```

### 4.2 计数器场景（incrBy/decrBy）

```java
// 统计某任务的执行次数（原子操作，线程安全）
public void recordTaskExecution(Long taskId) {
    String counterKey = "task_exec_count:" + taskId;
    // 原子加1，key 不存在时自动创建，过期时间 86400 秒
    Long count = squirrelOpService.incrBy(counterKey, 1L, 86400);
    log.info("任务{}已执行{}次", taskId, count);
}

// 限流场景：每分钟最多处理100个请求
public boolean checkRateLimit(String userId) {
    String limitKey = "rate_limit:" + userId + ":" + currentMinute();
    Long count = squirrelOpService.incrBy(limitKey, 1L, 60);
    return count <= 100;
}
```

---

## 第五节：最佳实践与踩坑指南

### 5.1 key 命名规范

```
格式：{业务}:{实体}:{ID}

✅ 好的 key 命名：
  staff_id_lock-12345          （云办公申请锁）
  task_exec_count:678          （任务执行计数）
  staff_level_cache:zhangsan   （员工等级缓存）

❌ 差的 key 命名：
  lock                          （太通用，全局冲突）
  temp_123                      （不知道是什么业务）
  key_2024010112345             （带时间戳的 key 堆积）
```

### 5.2 过期时间设计

| 数据类型 | 建议过期时间 | 说明 |
|---------|------------|------|
| 分布式锁 | 业务最长耗时的2倍 | 防死锁 |
| 用户会话缓存 | 30分钟 | 与登录 session 同步 |
| 配置数据缓存 | 5-10分钟 | 允许短暂不一致 |
| 计数器 | 24小时或更长 | 根据统计周期 |

本项目锁设置了 30分钟（`60*30`），因为云办公申请最长可能需要20分钟处理。

### 5.3 异常处理要点

```java
// Squirrel 操作可能抛出两种异常：
// 1. com.dianping.squirrel.asyncclient.exception.StoreException
// 2. com.dianping.squirrel.common.exception.StoreException

// SquirrelOpService 对此已做封装，调用方通常不需要处理异常
// 但对于分布式锁，获锁失败返回 false，不抛异常

public Boolean setNtxString(String key, Object value, int expireSecond) {
    try {
        StoreKey storeKey = new StoreKey(redisCategory, key);
        return storeClient.setnx(storeKey, value, expireSecond);
    } catch (Exception e) {
        log.error("setNtxString error, key:{}", key, e);
        return false;  // 异常也返回 false，安全降级
    }
}
```

**Fail-Open vs Fail-Close**：
- 锁操作异常返回 `false`（获锁失败） → **Fail-Close**，安全，宁可拒绝也不重复处理
- 某些只读操作异常可以忽略 → **Fail-Open**，降级到数据库查询

### 5.4 Squirrel vs @MdpConfig 的选择

| 场景 | 用 Squirrel（Redis）| 用 Lion（@MdpConfig） |
|------|------------------|--------------------|
| 分布式锁 | ✅ | ❌ |
| 运行时动态数据（用户状态）| ✅ | ❌ |
| 配置开关/阈值 | ❌ | ✅ |
| 静态文案/URL | ❌ | ✅ |
| 需要持久化的业务数据 | ❌（放 DB）| ❌ |

### 5.5 本项目 Squirrel 使用场景汇总

```
分布式锁：
  staff_id_lock-{staffId}     云办公申请防重复提交
                               粒度：员工ID级别
                               过期：30分钟

缓存（通过 SquirrelOpService）：
  可能存在其他业务场景的缓存，通过 getString/setString 操作
  统一 category = Lion 配置的 "REDIS_CATEGORY" 值
```
