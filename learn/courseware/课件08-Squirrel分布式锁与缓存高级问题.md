# 课件 08｜Squirrel 分布式锁与缓存高级问题

---

## 课程目标

学完本课件后，你能够：

1. 解释分布式锁的原理（SETNX + 超时）并正确使用
2. 设计合理的锁粒度避免性能问题
3. 识别并解决缓存穿透、缓存击穿、缓存雪崩三大问题
4. 使用 Squirrel 实现分布式 ID 生成和重复提交防护

---

## 一、为什么需要分布式锁？

### 1.1 多实例竞争问题

```
场景：某员工每天只能领一次奖励

单机 synchronized：
  ✅ 同一 JVM 内有效
  ❌ 跨实例无效（每台各有一把锁）

┌─────────────────────────────────────────────────────┐
│  实例 A：synchronized {                             │
│      if(!rewarded) grant(staffMis);  ← 执行！       │
│  }                                                  │
│  实例 B：synchronized {                             │
│      if(!rewarded) grant(staffMis);  ← 也执行！     │
│  }                                                  │
│  结果：同一员工领了 2 次奖励                          │
└─────────────────────────────────────────────────────┘
```

### 1.2 分布式锁原理

```
基于 Redis SETNX（Set if Not eXists）：

实例 A: SET lock:reward:wangwj "instance-A" NX EX 30
         → 成功（得到锁）
实例 B: SET lock:reward:wangwj "instance-B" NX EX 30
         → 失败（锁已被 A 持有）

实例 A 处理完成:
  DEL lock:reward:wangwj  → 释放锁

关键保障：
  NX  → 原子性"仅当不存在时设置"
  EX  → 超时自动释放（防死锁）
  唯一 value → 只有持锁者才能释放
```

---

## 二、Squirrel 分布式锁使用

### 2.1 基础用法

```java
@Service
@Slf4j
public class DailyRewardService {

    @Autowired
    private SquirrelDistributedLock distributedLock;

    /**
     * 发放每日奖励（带分布式锁）
     */
    public boolean grantDailyReward(String staffMis) {
        String lockKey = "daily-reward:" + staffMis;
        int lockTimeoutSeconds = 30;

        // tryLock：非阻塞，立即返回是否获取成功
        boolean locked = distributedLock.tryLock(lockKey, lockTimeoutSeconds);
        if (!locked) {
            log.warn("获取锁失败，跳过: staffMis={}", staffMis);
            return false;
        }

        try {
            // ⚠️ 锁内再次检查（Double-Check）
            if (rewardRepository.hasGrantedToday(staffMis)) {
                return false;
            }

            // 执行业务逻辑
            rewardRepository.insert(staffMis, LocalDate.now());
            pointService.addPoints(staffMis, 10);
            return true;

        } finally {
            // ⚠️ 必须在 finally 中释放，防止异常导致锁未释放
            distributedLock.unlock(lockKey);
        }
    }
}
```

### 2.2 锁粒度设计

```java
// ❌ 锁粒度太粗：所有操作共用一把锁
String lockKey = "reward-service-lock";  // 串行化所有请求！

// ✅ 锁粒度适中：按业务实体隔离
String lockKey = "reward:staff:" + staffMis;  // 不同员工互不影响

// ✅ 锁粒度更细：按日期+员工
String lockKey = "reward:staff:" + staffMis + ":" + LocalDate.now();
```

**原则：锁的粒度越细，并发性越好，但设计越复杂。找到业务临界点。**

### 2.3 可重入锁

```java
// 场景：同一线程内多次获取同一把锁
// 使用 Redisson 的可重入锁

@Autowired
private RedissonClient redissonClient;

public void nestedOperation(String resourceId) {
    RLock lock = redissonClient.getLock("resource:" + resourceId);
    try {
        lock.lock(30, TimeUnit.SECONDS);
        doLevel1Operation();  // 内部可能再次获取同一把锁
    } finally {
        lock.unlock();
    }
}
```

---

## 三、缓存三大问题

### 3.1 缓存穿透

```
定义：查询不存在的数据，缓存中也没有，每次都打到 DB

攻击场景：
  恶意请求：staffMis = "不存在的ID_1", "不存在的ID_2"...
  → 缓存全未命中
  → DB 每次全表扫描
  → DB 被打垮

解决方案：

方案1：缓存空值
  查询 → DB 无数据 → 缓存 null（TTL 短，如5分钟）
  代价：略微增加内存占用

方案2：布隆过滤器（BloomFilter）
  启动时将所有合法 ID 加入 BloomFilter
  查询前先检查 BloomFilter
  不在 BloomFilter 中 → 一定不存在 → 直接返回 null
  在 BloomFilter 中 → 可能存在 → 再查缓存/DB
```

```java
// 方案1：缓存空值实现
public StaffInfoDTO getStaffInfo(String staffMis) {
    Optional<String> cached = squirrelClient.get(STAFF_INFO_RAW_KEY, staffMis);
    if (cached.isPresent()) {
        return "NULL".equals(cached.get()) ? null :
               JSON.parseObject(cached.get(), StaffInfoDTO.class);
    }

    StaffInfo staff = staffRepository.findByMis(staffMis);
    if (staff == null) {
        squirrelClient.setWithExpire(STAFF_INFO_RAW_KEY, staffMis, "NULL", 300); // 5分钟
        return null;
    }

    StaffInfoDTO dto = toDTO(staff);
    squirrelClient.set(STAFF_INFO_RAW_KEY, staffMis, JSON.toJSONString(dto));
    return dto;
}
```

### 3.2 缓存击穿

```
定义：热点 key 过期的瞬间，大量并发请求同时穿透到 DB

┌─────────────────────────────────────────────────────┐
│  缓存中 "hot-data" 在 10:00:00 过期                  │
│  10:00:00.001 → 1000 个请求同时查缓存                │
│  1000 个都未命中                                      │
│  1000 个同时查 DB                                     │
│  → DB 瞬间压力 * 1000                                │
└─────────────────────────────────────────────────────┘

解决方案：

方案1：热点 key 不过期 + 异步更新
方案2：互斥锁（单个请求查 DB，其余等待）
```

```java
// 互斥锁解决缓存击穿
public StaffInfoDTO getHotDataWithMutex(String staffMis) {
    Optional<StaffInfoDTO> cached = squirrelClient.get(STAFF_INFO, staffMis);
    if (cached.isPresent()) {
        return cached.get();
    }

    // 未命中：用分布式锁保证只有一个线程查 DB
    String lockKey = "cache-mutex:staff:" + staffMis;
    boolean locked = distributedLock.tryLock(lockKey, 5);
    if (locked) {
        try {
            // 双检（可能锁等待期间已被其他线程更新了缓存）
            cached = squirrelClient.get(STAFF_INFO, staffMis);
            if (cached.isPresent()) {
                return cached.get();
            }

            // 查 DB 并更新缓存
            StaffInfo staff = staffRepository.findByMis(staffMis);
            StaffInfoDTO dto = toDTO(staff);
            squirrelClient.set(STAFF_INFO, staffMis, dto);
            return dto;
        } finally {
            distributedLock.unlock(lockKey);
        }
    } else {
        // 没抢到锁：短暂等待后重试（此时锁持有者正在查 DB）
        Thread.sleep(50);
        return getHotDataWithMutex(staffMis);  // 递归重试
    }
}
```

### 3.3 缓存雪崩

```
定义：大量 key 同时过期，或 Redis 宕机，导致流量全部打到 DB

┌─────────────────────────────────────────────────────┐
│  问题1：批量设置 TTL 相同                             │
│    缓存A TTL=3600, 缓存B TTL=3600, 缓存C TTL=3600   │
│    → 同时在 01:00:00 过期                            │
│    → 01:00:01 所有请求打 DB                          │
│                                                      │
│  问题2：Redis 宕机                                   │
│    → 所有缓存失效                                     │
│    → 全部流量打 DB                                   │
└─────────────────────────────────────────────────────┘

解决方案：

1. TTL 加随机数（分散过期时间）
   expireSeconds = 3600 + (int)(Math.random() * 600)

2. Redis 高可用（主从 + 哨兵 / Cluster）

3. 本地缓存兜底（Caffeine/Guava）
```

```java
// 加随机 TTL 防雪崩
private int randomTtl(int baseTtl) {
    // 在基础 TTL 上随机 ±10%
    int jitter = (int)(baseTtl * 0.1 * (Math.random() * 2 - 1));
    return baseTtl + jitter;
}

squirrelClient.setWithExpire(STAFF_INFO, staffMis, dto, randomTtl(3600));
```

---

## 四、高级应用场景

### 4.1 分布式 ID 生成

```java
// 利用 Redis INCRBY 生成全局唯一 ID
public Long generateId(String bizType) {
    return squirrelClient.incrBy(
            ID_GEN_KEY,
            bizType,
            1L
    );
}

// 批量获取 ID（减少 Redis 调用次数）
public List<Long> batchGenerateIds(String bizType, int count) {
    Long maxId = squirrelClient.incrBy(ID_GEN_KEY, bizType, (long) count);
    List<Long> ids = new ArrayList<>(count);
    for (int i = count - 1; i >= 0; i--) {
        ids.add(maxId - i);
    }
    return ids;
}
```

### 4.2 重复提交防护

```java
// 防止用户重复点击提交按钮
public boolean checkAndMarkRequest(String requestKey) {
    String lockKey = "submit:lock:" + requestKey;
    // SET NX + EX：原子操作
    // 成功：第一次提交，返回 true
    // 失败：重复提交，返回 false
    boolean isFirstSubmit = squirrelClient.setNx(lockKey, "1", 10); // 10秒内防重
    if (!isFirstSubmit) {
        log.warn("重复提交被拦截: key={}", requestKey);
    }
    return isFirstSubmit;
}

// 使用
@PostMapping("/submit")
public Result submit(@RequestBody SubmitRequest request) {
    String requestKey = request.getStaffMis() + ":" + request.getFormType();
    if (!cacheService.checkAndMarkRequest(requestKey)) {
        return Result.fail("请勿重复提交");
    }
    return processSubmit(request);
}
```

---

## 五、缓存问题速查表

| 问题 | 现象 | 解决方案 |
|------|------|---------|
| **缓存穿透** | 不存在的数据频繁查 DB | 缓存空值 / 布隆过滤器 |
| **缓存击穿** | 热点 key 过期时 DB 瞬间压力暴增 | 互斥锁 / 不过期 + 异步刷新 |
| **缓存雪崩** | 大量 key 同时过期 | 随机 TTL / Redis 高可用 |
| **数据不一致** | 缓存有旧数据 | 更新 DB 后删除缓存 |
| **锁死锁** | 锁未释放，其他进程永远等待 | 必须设置 EX 超时 |

---

## 六、核心知识点总结

```
分布式锁原理：
  SETNX + EX = 原子加锁 + 超时保护
  唯一 value → 只有持锁者才释放
  finally 释放 → 防止异常泄漏
  锁粒度 → 按业务实体，不要太粗

缓存三大问题：
  穿透 → 缓存 null 值（短 TTL）
  击穿 → 互斥锁（Double-Check）
  雪崩 → 随机 TTL / Redis 高可用

高级场景：
  分布式 ID → Redis INCRBY
  重复提交防护 → SETNX + 短 TTL
  限流计数 → INCRBY + 原子操作
```

---

## 课后练习

1. **设计：** 实现一个接口限流器，限制每个用户每分钟最多调用 100 次，使用 Squirrel 实现
2. **排查：** 发现某接口 DB 负载很高，但 Redis 命中率看起来正常。可能是哪种缓存问题？如何定位？
3. **思考：** 分布式锁中为什么要用"唯一 value"？如果不用会发生什么问题？

---

*← 上一课：Squirrel 缓存基础 | 下一课：Lion 配置中心 →*
