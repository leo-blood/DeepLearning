# 课件 02｜Pigeon RPC 框架基础

---

## 课程目标

学完本课件后，你能够：

1. 解释 RPC 的工作原理（Stub、序列化、服务发现）
2. 使用 `@MdpPigeonServer` 正确暴露一个服务接口
3. 使用 `@MdpPigeonClient` 在客户端安全注入并调用
4. 处理 RPC 调用中的常见异常

---

## 一、RPC 是什么？

### 1.1 本地调用 vs 远程调用

```java
// 本地调用（同一 JVM）
UserService userService = new UserServiceImpl();
User user = userService.getUser(123L);  // 直接方法调用

// 远程调用（跨网络）—— 没有框架时
HttpClient http = new HttpClient();
String json = http.get("http://user-service/api/getUser?id=123");
User user = JSON.parseObject(json, User.class);  // 手动序列化
```

RPC 框架的目标：让远程调用**看起来像本地调用**。

### 1.2 RPC 调用链路

```
调用方（Consumer）                      提供方（Provider）
┌──────────────────────┐               ┌──────────────────────┐
│  业务代码              │               │  业务实现              │
│  userService.getUser()│               │  UserServiceImpl      │
│         ↓             │               │         ↑             │
│   Pigeon Client Stub  │──── 网络 ────│  Pigeon Server Stub   │
│   序列化请求           │               │  反序列化请求          │
│   查服务注册中心        │               │  执行业务逻辑          │
│   连接池管理           │               │  序列化响应            │
└──────────────────────┘               └──────────────────────┘
         ↑                                       ↑
         └──────── MNS 服务注册中心 ─────────────┘
                  (记录"UserService 在哪台机器")
```

---

## 二、服务端：暴露接口

### 2.1 定义接口（Interface）

```java
// 放在 api 模块中，供双方共享
public interface StaffSkillService {

    /**
     * 查询员工技能列表
     * @param staffMis 员工工号
     * @return 技能列表，不存在时返回空集合（不返回 null）
     */
    List<SkillDTO> querySkillsByStaffMis(String staffMis);

    /**
     * 批量查询技能
     * @param staffMisList 工号列表，最多 100 个
     */
    Map<String, List<SkillDTO>> batchQuerySkills(List<String> staffMisList);
}
```

**接口设计原则：**
- 方法参数和返回值必须可序列化（实现 `Serializable` 或使用 DTO）
- 不返回 `null`，用空集合/Optional 代替
- 参数加校验注释（最大数量、必填项）

### 2.2 实现服务（Server）

```java
@MdpPigeonServer  // ← 关键注解，框架自动注册到 MNS
public class StaffSkillServiceImpl implements StaffSkillService {

    @Autowired
    private StaffSkillRepository repository;

    @Override
    public List<SkillDTO> querySkillsByStaffMis(String staffMis) {
        // 1. 参数校验
        if (StringUtils.isBlank(staffMis)) {
            return Collections.emptyList();
        }

        // 2. 业务逻辑
        List<StaffSkill> skills = repository.findByStaffMis(staffMis);

        // 3. 转换为 DTO（不要直接返回 DB 实体）
        return skills.stream()
                .map(this::toDTO)
                .collect(Collectors.toList());
    }

    @Override
    public Map<String, List<SkillDTO>> batchQuerySkills(List<String> staffMisList) {
        if (CollectionUtils.isEmpty(staffMisList)) {
            return Collections.emptyMap();
        }
        // 批量查询，避免 N+1
        List<StaffSkill> allSkills = repository.findByStaffMisIn(staffMisList);
        return allSkills.stream()
                .map(this::toDTO)
                // 按 staffMis 分组
                .collect(Collectors.groupingBy(SkillDTO::getStaffMis));
    }
}
```

### 2.3 服务端配置

```yaml
# application.yml
pigeon:
  service:
    name: ${spring.application.name}  # 服务名，客户端用来查找
  server:
    port: 4040
    timeout: 3000  # 服务端处理超时（ms）
```

---

## 三、客户端：调用服务

### 3.1 注入依赖

```java
@Service
public class SkillAggregationService {

    // @MdpPigeonClient = 自动从 MNS 查找服务地址 + 创建代理
    @MdpPigeonClient(service = "staff-skill-service")
    private StaffSkillService staffSkillService;

    // ❌ 错误：不要用 @Autowired 注入 RPC 接口
    // @Autowired
    // private StaffSkillService staffSkillService;
}
```

### 3.2 防御性调用

```java
public List<SkillDTO> getSkillsSafely(String staffMis) {
    try {
        List<SkillDTO> skills = staffSkillService.querySkillsByStaffMis(staffMis);
        // ❌ 不要信任返回值，防止对方违约返回 null
        return skills != null ? skills : Collections.emptyList();
    } catch (PigeonException e) {
        // RPC 框架层异常（网络超时、序列化失败等）
        log.warn("Pigeon call failed for staffMis={}, reason={}", staffMis, e.getMessage());
        return Collections.emptyList();  // 降级：返回空，业务继续
    } catch (Exception e) {
        // 服务端抛出的业务异常
        log.error("Unexpected error querying skills for staffMis={}", staffMis, e);
        return Collections.emptyList();
    }
}
```

**防御性编程三要素：**

| 要素 | 说明 |
|------|------|
| try-catch | 捕获 PigeonException，不要让 RPC 异常崩溃调用方 |
| null check | 对方可能违约返回 null，始终校验 |
| 降级逻辑 | 异常时返回默认值，不影响主流程 |

### 3.3 批量调用优化

```java
// ❌ 反模式：循环单个调用（N 次 RPC）
public Map<String, List<SkillDTO>> querySkillsNaive(List<String> staffMisList) {
    Map<String, List<SkillDTO>> result = new HashMap<>();
    for (String mis : staffMisList) {
        result.put(mis, staffSkillService.querySkillsByStaffMis(mis));  // N 次网络调用！
    }
    return result;
}

// ✅ 正确：一次批量调用
public Map<String, List<SkillDTO>> querySkillsBatch(List<String> staffMisList) {
    if (CollectionUtils.isEmpty(staffMisList)) {
        return Collections.emptyMap();
    }
    try {
        return staffSkillService.batchQuerySkills(staffMisList);
    } catch (PigeonException e) {
        log.warn("Batch query failed, size={}", staffMisList.size());
        return Collections.emptyMap();
    }
}
```

---

## 四、异常处理详解

### 4.1 异常类型

```
调用异常分类：
├── PigeonException（框架层）
│   ├── TimeoutException    ← 超过 timeout 未返回
│   ├── ServiceNotFoundException ← MNS 找不到服务
│   └── SerializationException   ← 序列化/反序列化失败
│
└── 业务异常（服务端主动抛出）
    ├── IllegalArgumentException ← 参数错误
    └── 自定义 BusinessException
```

### 4.2 超时配置

```java
// 客户端超时配置（优先级：方法级 > 接口级 > 全局）
@MdpPigeonClient(
    service = "staff-skill-service",
    timeout = 2000  // 该接口所有方法超时 2s
)
private StaffSkillService staffSkillService;
```

```yaml
# 全局默认超时
pigeon:
  client:
    default-timeout: 3000  # ms
```

---

## 五、常见错误与排查

| 错误现象 | 可能原因 | 排查方法 |
|---------|---------|---------|
| `ServiceNotFoundException` | 服务未注册/服务名拼错 | 检查 `@MdpPigeonServer` 和客户端 service 名 |
| `TimeoutException` | 服务端处理慢/网络问题 | 查服务端 GC 日志，检查 SQL 慢查询 |
| `ClassCastException` | DTO 版本不一致 | 检查 api jar 版本是否同步 |
| `NullPointerException` | 未做 null 检查 | 服务端保证不返回 null，客户端做防御 |
| 调用方收到业务异常 | 服务端抛出并透传 | 查服务端日志，异常信息含服务端堆栈 |

---

## 六、核心知识点总结

```
Pigeon 服务端：
  @MdpPigeonServer → 自动注册到 MNS
  实现接口 → 提供业务逻辑
  不返回 null → 契约保证

Pigeon 客户端：
  @MdpPigeonClient → 自动创建代理
  防御性调用 → try-catch + null check + 降级
  批量接口 → 避免 N 次 RPC

核心概念：
  服务发现 (MNS) → 动态寻址
  Hessian 序列化 → 性能优化
  超时设置 → 防止雪崩
```

---

## 课后练习

1. **基础：** 仿照示例，设计一个 `OrderQueryService` 接口，包含单查和批量查询方法，考虑参数校验和返回值设计
2. **进阶：** 当批量接口部分失败时（如100条中有5条查询超时），如何设计接口使调用方知道哪些成功哪些失败？
3. **思考：** 为什么 Pigeon 使用 Hessian 序列化而不是 JSON？（提示：对比二进制协议和文本协议的优缺点）

---

*← 上一课：分布式系统全景 | 下一课：Pigeon 高级特性 →*
