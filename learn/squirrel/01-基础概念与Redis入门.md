# Squirrel 基础概念与 Redis 入门

---

## 1. Redis 基础回顾

### 1.1 Redis 是什么

Redis（Remote Dictionary Server）是一个基于内存的 key-value 存储系统：

```
特点：
  - 内存存储：读写速度 μs 级（数据库是 ms 级）
  - 单线程处理命令：天然线程安全，支持原子操作
  - 丰富的数据结构：String/Hash/List/Set/ZSet
  - 支持持久化：数据可落盘，重启不丢失
  - 支持过期时间：key 可以自动失效
```

### 1.2 Redis 的核心操作

```bash
# String 操作
SET key value EX 60      # 设置 key，60秒过期
GET key                  # 读取 key
DEL key                  # 删除 key
SETNX key value          # 不存在时才设置（SET if Not eXists）
INCRBY key 1             # 原子加1（线程安全）

# 特点：所有命令都是原子操作，多线程安全
```

### 1.3 为什么 Redis 适合做缓存和锁

```
适合做缓存：
  热点数据放内存 → 速度比 MySQL 快 1000x
  自动过期 → 数据定期刷新，不会永远是旧数据

适合做分布式锁：
  SETNX 是原子操作 → 只有一个线程能设置成功
  支持过期时间 → 持锁方崩溃也能自动释放
  多台机器共享 → 不同机器的线程竞争同一把锁
```

---

## 2. 直接用 Redis vs Squirrel

### 2.1 直接用 Jedis（原始方式）

```java
// 烦琐且有风险
JedisPool pool = new JedisPool("redis-host", 6379);

try (Jedis jedis = pool.getResource()) {
    jedis.auth("password");
    // key 命名全靠自觉，容易跨业务冲突
    jedis.setex("userId:123", 60, JSON.toJSONString(userInfo));
    String result = jedis.get("userId:123");
}
// 忘了 try-with-resources → 连接泄漏
```

问题：
- 连接池需要手动管理
- key 命名无约束，不同业务容易冲突
- 序列化/反序列化需要手写
- 集群切换需要修改代码

### 2.2 Squirrel 的设计理念

Squirrel 是美团对 Redis 的封装，核心抽象是 **StoreKey**：

```java
// StoreKey = category（业务命名空间）+ key（业务主键）
StoreKey storeKey = new StoreKey("cratos-supplement", "userId:123");

// 实际存入 Redis 的 key 是：cratos-supplement:userId:123
// category 隔离了不同业务的 key，彻底解决命名冲突
```

Squirrel 提供：
- **自动连接池管理**：无需手动管理 Jedis 连接
- **Category 命名隔离**：不同业务的 key 天然隔离
- **类型安全**：支持泛型，自动序列化/反序列化
- **集群透明**：底层切换 Redis 集群不影响业务代码

---

## 3. Squirrel 核心概念

### 3.1 StoreKey：操作的基本单位

```java
// 格式：new StoreKey(category, key)

// 业务示例
new StoreKey("cratos-supplement", "staff_id_lock-12345")
//  ↑ 业务命名空间               ↑ 具体的业务 key

// 最终在 Redis 中存储的 key：
// cratos-supplement:staff_id_lock-12345
```

### 3.2 Category：业务命名空间

category 的作用是隔离不同业务：

```
Redis 实例（物理上一个集群）

  cratos-supplement:staff_id_lock-123    ← ccsadmin 业务的锁
  cratos-supplement:staff_level:zhangsan ← ccsadmin 业务的缓存
  cratos-incentive:task_cache:456        ← 其他业务
  cratos-schedule:class_lock:789         ← 排班业务的锁

不同 category 的 key 完全独立，互不干扰
```

本项目 category 通过 Lion 动态配置：

```java
// SquirrelOpService.java
@MdpConfig("REDIS_CATEGORY:cratos-supplement")
private String redisCategory;  // 默认 cratos-supplement

// 所有 key 统一用这个 category
StoreKey storeKey = new StoreKey(redisCategory, key);
```

### 3.3 RedisStoreClient：操作客户端

```java
// squirrel.properties 中配置连接信息，框架自动创建 Bean
@Autowired
@Qualifier("cscRedis")           // 对应 squirrel.properties 中配置的名称
private RedisStoreClient storeClient;
```

---

## 4. squirrel.properties 配置

```properties
# squirrel.properties（prod 环境）

# 配置一个名为 cscRedis 的 Redis 连接
squirrel.store.redis.cscRedis.servers=redis-sentinel-host:26379
squirrel.store.redis.cscRedis.password=$KMS{redisPassword}   # KMS 加密
squirrel.store.redis.cscRedis.masterName=csc-redis-master

# 连接池配置
squirrel.store.redis.cscRedis.pool.maxActive=50    # 最大连接数
squirrel.store.redis.cscRedis.pool.maxIdle=20      # 最大空闲连接
squirrel.store.redis.cscRedis.pool.minIdle=5       # 最小空闲连接
squirrel.store.redis.cscRedis.pool.maxWait=2000    # 等待连接超时（ms）
```

---

## 5. SquirrelOpService 封装详解

本项目在 Squirrel 基础上又封装了一层 `SquirrelOpService`：

```java
@Service
@Slf4j
public class SquirrelOpService {

    @Autowired
    @Qualifier("cscRedis")
    private RedisStoreClient storeClient;

    @MdpConfig("REDIS_CATEGORY:cratos-supplement")
    private String redisCategory;          // category 由 Lion 动态配置

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

    // SET IF NOT EXISTS（分布式锁核心）
    public Boolean setNtxString(String key, Object value, int expireSecond) {
        try {
            StoreKey storeKey = new StoreKey(redisCategory, key);
            return storeClient.setnx(storeKey, value, expireSecond);
        } catch (Exception e) {
            log.error("setNtxString error, key:{}", key, e);
            return false;   // 异常时返回 false，安全降级
        }
    }

    // 原子加（计数器）
    public Long incrBy(String key, long amount, int expireSecond) { ... }

    // 原子减（计数器）
    public Long decrBy(String key, long amount, int expireSecond) { ... }
}
```

**封装的好处**：
- category 由一处管理（SquirrelOpService），改 Lion 配置即全部生效
- 异常处理统一（`setNtxString` 异常时返回 false，不向上抛）
- 调用方代码更简洁（不需要每次 new StoreKey）

---

## 6. Squirrel vs 其他方案对比

| 对比项 | Squirrel | 直接 Jedis | Spring Cache |
|--------|---------|-----------|-------------|
| 连接管理 | 自动 | 手动 | 自动 |
| 命名隔离 | ✅ Category | ❌ 手动规范 | 部分支持 |
| 分布式锁 | ✅ | ✅（手写） | ❌ |
| 集群支持 | ✅ | 需配置 | ✅ |
| 美团基础设施集成 | ✅ | ❌ | ❌ |
| 适合场景 | 内部服务 | 灵活场景 | 注解缓存 |
