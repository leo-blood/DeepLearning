# 课件 03｜Pigeon 高级特性：负载均衡、熔断降级与链路追踪

---

## 课程目标

学完本课件后，你能够：

1. 解释负载均衡的几种策略及其适用场景
2. 理解熔断器（Circuit Breaker）的状态机和工作原理
3. 配置服务版本隔离实现灰度发布
4. 利用链路追踪定位跨服务调用的性能瓶颈

---

## 一、负载均衡

### 1.1 为什么需要负载均衡？

```
服务提供方（3 台实例）：
  ┌──────────┐
  │ 实例 A   │  ←── 如果流量全打到 A，BC 闲置
  │ 实例 B   │  ←── 需要均匀分发
  │ 实例 C   │
  └──────────┘

负载均衡目标：
  1. 均匀利用所有实例（避免热点）
  2. 剔除故障实例（健康检查）
  3. 优先本机房（降低跨机房延迟）
```

### 1.2 Pigeon 支持的负载均衡策略

| 策略 | 说明 | 适用场景 |
|------|------|---------|
| **Random（随机）** | 随机选择一台 | 无状态服务，默认推荐 |
| **RoundRobin（轮询）** | 依次轮流 | 实例性能均匀 |
| **WeightedRandom（加权随机）** | 按权重随机 | 灰度发布（新版本低权重） |
| **ActiveWeight（活跃度权重）** | 响应快的实例权重高 | 实例性能不均 |
| **LocalFirst（本机房优先）** | 同机房优先，跨机房兜底 | 多机房部署（**强烈推荐**） |

### 1.3 配置负载均衡策略

```yaml
# 全局配置
pigeon:
  client:
    loadbalance: random  # random/roundrobin/localfirst

# 或在注解上指定
@MdpPigeonClient(
    service = "staff-skill-service",
    loadbalance = LoadBalance.LOCAL_FIRST  // 本机房优先
)
```

### 1.4 权重配置（灰度发布）

```
场景：新版本上线，先给 10% 流量，观察稳定后再扩大

实例 A (老版本): weight=9
实例 B (新版本): weight=1
─────────────────────────────
90% 流量 → 实例 A
10% 流量 → 实例 B
```

---

## 二、熔断与降级

### 2.1 没有熔断会发生什么？

```
正常情况：
  A ──Pigeon──► B (正常响应 100ms)

B 服务出问题（响应变慢 5000ms）：
  A ──Pigeon──► B (等待 5000ms...)
  ↑ 线程被占用
  同时 C、D 也在调 B...
  ─────────────────────────────
  所有服务线程耗尽 → 雪崩！
```

### 2.2 熔断器状态机

```
     超过失败阈值
CLOSED ─────────────► OPEN
(正常)                (熔断)
  ▲                     │
  │ 半开期成功           │ 等待冷却时间
  │                     ▼
  └──────────────── HALF-OPEN
                    (试探性请求)

CLOSED：正常调用
OPEN  ：快速失败，不发起 RPC，直接返回降级值
HALF-OPEN：放行少量请求探测服务是否恢复
```

### 2.3 配置熔断

```java
@MdpPigeonClient(
    service = "staff-skill-service",
    timeout = 2000,
    // 熔断配置
    circuitBreaker = @CircuitBreaker(
        failureRateThreshold = 50,  // 失败率 > 50% 触发熔断
        waitDurationInOpenState = 30,  // 熔断 30 秒后进入 HALF-OPEN
        permittedCalls = 5  // HALF-OPEN 期间放行 5 次试探
    )
)
private StaffSkillService staffSkillService;
```

### 2.4 降级策略

```java
@Service
public class SkillServiceWithFallback {

    @MdpPigeonClient(service = "staff-skill-service")
    private StaffSkillService staffSkillService;

    // 策略1：返回空值降级（适合非核心数据）
    public List<SkillDTO> getSkillsWithFallback(String staffMis) {
        try {
            return staffSkillService.querySkillsByStaffMis(staffMis);
        } catch (PigeonException e) {
            log.warn("Fallback: skill service unavailable for {}", staffMis);
            return Collections.emptyList();  // 降级：返回空
        }
    }

    // 策略2：缓存降级（适合读多写少的数据）
    @Autowired
    private SkillCache skillCache;

    public List<SkillDTO> getSkillsFromCacheOrRpc(String staffMis) {
        try {
            List<SkillDTO> skills = staffSkillService.querySkillsByStaffMis(staffMis);
            skillCache.put(staffMis, skills);  // 更新缓存
            return skills;
        } catch (PigeonException e) {
            log.warn("RPC failed, fallback to cache for {}", staffMis);
            return skillCache.getOrDefault(staffMis, Collections.emptyList());
        }
    }
}
```

---

## 三、服务版本隔离（灰度发布）

### 3.1 问题场景

```
需求：v2 版本有 Breaking Change，需要：
  1. v2 服务端先上线（但不影响现有调用方）
  2. 逐步将调用方迁移到 v2
  3. 老调用方继续用 v1，不感知变化
```

### 3.2 版本配置

```java
// 服务端 v1（旧接口）
@MdpPigeonServer(version = "1.0.0")
public class StaffSkillServiceImplV1 implements StaffSkillService {
    // 旧实现
}

// 服务端 v2（新接口）
@MdpPigeonServer(version = "2.0.0")
public class StaffSkillServiceImplV2 implements StaffSkillServiceV2 {
    // 新实现，兼容新参数
}

// 客户端指定版本
@MdpPigeonClient(service = "staff-skill-service", version = "2.0.0")
private StaffSkillServiceV2 staffSkillServiceV2;

@MdpPigeonClient(service = "staff-skill-service", version = "1.0.0")
private StaffSkillService staffSkillServiceV1;
```

---

## 四、全链路追踪

### 4.1 为什么需要链路追踪？

```
一次用户请求的调用链：
  请求 → Gateway → ServiceA → ServiceB → ServiceC → DB
                      ↘→ ServiceD
  
问题：用户反馈"下单很慢"，到底慢在哪一步？
  ─────────────────────────────────────────────
  没有链路追踪：挨个查日志，无法关联
  有链路追踪：TraceId 串联所有步骤，一眼定位
```

### 4.2 Pigeon 自动注入 TraceId

```
Pigeon 自动在请求头传递 TraceId：

ServiceA（TraceId: abc123）
  ├── 调用 ServiceB → 请求头携带 TraceId: abc123
  └── 调用 ServiceC → 请求头携带 TraceId: abc123

ServiceB 日志：[TraceId: abc123] 处理耗时 500ms
ServiceC 日志：[TraceId: abc123] 处理耗时 2000ms  ← 找到瓶颈！
```

### 4.3 日志中打印 TraceId

```java
// 在日志中自动包含 TraceId（MDC 方式）
// logback-spring.xml 配置：
// <pattern>%d{yyyy-MM-dd HH:mm:ss} [%thread] [TraceId:%X{traceId}] %-5level %logger{36} - %msg%n</pattern>

@Override
public List<SkillDTO> querySkillsByStaffMis(String staffMis) {
    // 日志自动带 TraceId，不需要手动传参
    log.info("Query skills for staffMis={}", staffMis);
    // ...
}
```

### 4.4 链路追踪排查流程

```
1. 从前端 / 告警获取出问题的 RequestId 或 TraceId
         ↓
2. 到日志系统（CATS）搜索 TraceId
         ↓
3. 查看调用链瀑布图（耗时分布）
         ↓
4. 定位最慢的节点
         ↓
5. 查看该节点的详细日志
```

---

## 五、Pigeon 监控指标

| 指标 | 含义 | 告警阈值建议 |
|------|------|------------|
| `pigeon.client.request.rt` | 客户端请求耗时 | P99 > timeout * 0.8 |
| `pigeon.client.request.qps` | 请求吞吐量 | 突增 > 历史 3倍 |
| `pigeon.client.error.rate` | 错误率 | > 1% 告警，> 5% 熔断 |
| `pigeon.server.thread.active` | 服务端活跃线程数 | > 线程池上限 80% |
| `pigeon.circuit.state` | 熔断状态 | OPEN 状态即告警 |

---

## 六、高级特性总结

```
负载均衡：
  LocalFirst → 多机房部署必选
  WeightedRandom → 灰度发布
  默认 Random → 普通场景

熔断降级：
  失败率阈值触发 → OPEN 状态快速失败
  冷却后 HALF-OPEN → 试探恢复
  客户端实现 fallback → 返回降级值

版本隔离：
  @MdpPigeonServer(version="x.x.x")
  @MdpPigeonClient(version="x.x.x")
  实现不间断迁移

链路追踪：
  Pigeon 自动传递 TraceId
  日志 MDC 配置自动打印
  CATS 系统查看调用链
```

---

## 课后练习

1. **实践：** 模拟熔断场景：让 B 服务 sleep(5000)，观察熔断器状态变化过程
2. **设计：** 如何设计一个接口使得 v1 和 v2 客户端都能正常工作？（提示：考虑接口兼容性）
3. **排查：** 给你一个 TraceId，用 CATS 查看调用链，找出耗时最长的节点并定位原因

---

*← 上一课：Pigeon 基础 | 下一课：Mafka 消息队列基础 →*
