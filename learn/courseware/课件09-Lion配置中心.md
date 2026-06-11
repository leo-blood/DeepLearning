# 课件 09｜Lion 配置中心：动态配置管理

---

## 课程目标

学完本课件后，你能够：

1. 解释配置中心解决的核心问题（零重启热更新）
2. 使用 `@MdpConfig` 注入各种类型的配置项
3. 监听配置变更并动态响应
4. 设计配置分层（功能开关、阈值、模板、密钥）

---

## 一、配置管理的演进史

### 1.1 各阶段对比

```
阶段1：代码硬编码
  private int maxRetry = 3;  // ← 修改需要改代码、重新编译部署
  
阶段2：properties 文件
  max.retry=3  // ← 修改需要重新打包发布
  
阶段3：环境变量
  MAX_RETRY=3  // ← 修改需要重启进程
  
阶段4：配置中心（Lion）
  lion 控制台修改 → 推送到所有实例 → 实时生效（无需重启）
```

### 1.2 Lion 的核心优势

| 能力 | 说明 |
|------|------|
| **热更新** | 修改后秒级推送，不需要重启服务 |
| **环境隔离** | dev/staging/prod 各自独立配置 |
| **版本历史** | 每次修改都有记录，支持回滚 |
| **权限控制** | 敏感配置只有特定人员可修改 |
| **KMS 加密** | 密码、密钥等敏感配置加密存储 |

---

## 二、配置类型与注入

### 2.1 基础类型配置

```java
@Component
public class AppConfig {

    // String 类型
    @MdpConfig("ccs.admin.welcome.message")
    private String welcomeMessage;

    // Integer 类型（自动类型转换）
    @MdpConfig("ccs.admin.task.max.daily")
    private Integer maxDailyTask;

    // Boolean 类型（功能开关）
    @MdpConfig("ccs.admin.feature.new.ui.enabled")
    private Boolean newUiEnabled;

    // Long 类型
    @MdpConfig("ccs.admin.cache.ttl.seconds")
    private Long cacheTtlSeconds;
}
```

### 2.2 集合类型配置

```yaml
# Lion 控制台配置值（JSON 格式）
# Key: ccs.admin.task.type.whitelist
# Value: ["CUSTOMER_SERVICE", "QUALITY_CHECK", "TRAINING"]
```

```java
// 数组配置
@MdpConfig("ccs.admin.task.type.whitelist")
private String[] taskTypeWhitelist;  // 自动解析 JSON Array

// List 配置
@MdpConfig("ccs.admin.allowed.mis.list")
private List<String> allowedMisList;

// Map 配置
// Lion 值: {"VIP": 100, "NORMAL": 50, "TRIAL": 10}
@MdpConfig("ccs.admin.quota.by.level")
private Map<String, Integer> quotaByLevel;
```

### 2.3 LionService 统一管理（推荐）

```java
// ✅ 推荐：将所有 Lion 配置集中在一个 Bean 中管理
@Component
@Slf4j
public class LionService {

    // ===== 功能开关 =====
    @MdpConfig("ccs.admin.feature.new.task.ui")
    private Boolean newTaskUiEnabled = false;  // 默认值（Lion 未配置时使用）

    @MdpConfig("ccs.admin.feature.smart.routing")
    private Boolean smartRoutingEnabled = false;

    // ===== 业务阈值 =====
    @MdpConfig("ccs.admin.task.daily.limit")
    private Integer taskDailyLimit = 20;

    @MdpConfig("ccs.admin.reward.points.per.task")
    private Integer rewardPointsPerTask = 10;

    // ===== 消息模板 =====
    @MdpConfig("ccs.admin.notification.task.complete.template")
    private String taskCompleteTemplate = "您的任务 {taskName} 已完成，获得 {points} 积分";

    // ===== 系统参数 =====
    @MdpConfig("ccs.admin.external.api.timeout.ms")
    private Integer externalApiTimeoutMs = 3000;

    // Getter 方法（供其他 Bean 调用）
    public boolean isNewTaskUiEnabled() {
        return Boolean.TRUE.equals(newTaskUiEnabled);
    }

    public String formatTaskCompleteMsg(String taskName, int points) {
        return taskCompleteTemplate
                .replace("{taskName}", taskName)
                .replace("{points}", String.valueOf(points));
    }
}
```

---

## 三、配置热更新监听

### 3.1 监听配置变更

```java
@Component
@Slf4j
public class DynamicConfigListener {

    @Autowired
    private LionService lionService;

    // 当指定配置变更时，自动调用此方法
    @MdpConfigChange("ccs.admin.task.daily.limit")
    public void onTaskLimitChange(ConfigChangeEvent event) {
        log.info("配置变更: key={}, oldValue={}, newValue={}",
                event.getKey(), event.getOldValue(), event.getNewValue());
        // 可以在这里做额外处理，如清除相关缓存
    }
}
```

### 3.2 动态功能开关

```java
@RestController
public class TaskController {

    @Autowired
    private LionService lionService;

    @GetMapping("/task/list")
    public Result getTaskList(@RequestParam String staffMis) {
        // 功能开关：新 UI 是否开启
        if (lionService.isNewTaskUiEnabled()) {
            return getTaskListV2(staffMis);
        }
        return getTaskListV1(staffMis);
    }
}
```

---

## 四、配置使用场景

### 4.1 场景一：功能开关（Feature Flag）

```java
// Lion 配置：ccs.admin.feature.ab.test.group.a = true

// A/B 测试：50% 用户走新流程
public String getRecommendationAlgorithm(String staffMis) {
    if (lionService.isAbTestGroupA(staffMis)) {
        return "new_algorithm";  // A 组：新算法
    }
    return "old_algorithm";  // B 组：老算法
}

// 灰度发布：先开给内部员工
public boolean shouldUseNewFeature(String staffMis) {
    List<String> betaMisList = lionService.getBetaTesters();
    return betaMisList.contains(staffMis);
}
```

### 4.2 场景二：阈值参数化

```java
// ❌ 硬编码：修改需要发版
public boolean isQuotaExceeded(String staffMis, int count) {
    return count > 100;  // 100 是魔法数字，不能动态调整
}

// ✅ Lion 配置化：可动态调整
public boolean isQuotaExceeded(String staffMis, int count) {
    int quota = lionService.getQuotaByMis(staffMis);  // 从 Lion 获取
    return count > quota;
}
// 限流告急时，直接在 Lion 控制台降低阈值，无需发版
```

### 4.3 场景三：消息模板管理

```java
// ❌ 硬编码消息
return "恭喜你完成了" + taskName + "，获得了" + points + "积分！";

// ✅ Lion 模板
// Lion 配置：ccs.admin.msg.task.complete = "恭喜你完成了{task}，获得了{points}积分！"

public String buildMessage(String taskName, int points) {
    String template = lionService.getTaskCompleteTemplate();
    return template.replace("{task}", taskName)
                   .replace("{points}", String.valueOf(points));
}
// 需要改文案时，直接改 Lion，不用发版
```

### 4.4 场景四：URL 和接口管理

```java
// ❌ 硬编码 URL
String apiUrl = "https://internal.api.company.com/v1/user";

// ✅ Lion 配置化
String apiUrl = lionService.getUserServiceUrl();
// 切换环境、更换接口版本 → 改 Lion，不发版
```

---

## 五、敏感配置加密（KMS）

```java
// 普通配置（明文存储）
@MdpConfig("ccs.admin.api.public.key")
private String apiPublicKey;

// 敏感配置（KMS 加密存储）
@MdpConfig(value = "ccs.admin.api.secret.key", kms = true)
private String apiSecretKey;  // Lion 存储加密值，注入时自动解密

// 数据库密码（必须加密！）
@MdpConfig(value = "ccs.admin.db.password", kms = true)
private String dbPassword;
```

**敏感配置清单（必须加密）：**
- 数据库密码
- 第三方 API Key / Secret
- 加密密钥
- OAuth Token
- 内网服务 Token

---

## 六、配置命名规范

```
格式：{appName}.{module}.{configName}

✅ 规范示例：
  ccs.admin.feature.new.ui.enabled     ← 功能开关
  ccs.admin.task.daily.limit           ← 业务阈值
  ccs.admin.notification.template      ← 消息模板
  ccs.admin.external.oa.api.url        ← 外部接口地址
  ccs.admin.db.password                ← 敏感配置（加密）

❌ 不规范示例：
  enabled                              ← 无命名空间
  CCS_ADMIN_FEATURE                    ← 不用大写
  ccs.test.abc                         ← 含义不明确
```

---

## 七、Lion 控制台操作

| 操作 | 说明 |
|------|------|
| **查看配置** | 搜索 Key，查看各环境的值 |
| **修改配置** | 编辑值 → 保存 → 自动推送所有实例 |
| **查看历史** | 每次修改记录修改人、时间、前后值 |
| **回滚** | 一键恢复到上一个版本 |
| **批量修改** | 导入 CSV 批量更新 |

---

## 八、核心知识点总结

```
Lion 核心价值：
  热更新 → 不重启修改参数
  环境隔离 → dev/staging/prod 各自配置
  版本回滚 → 可追溯、可恢复
  KMS 加密 → 敏感配置安全

@MdpConfig 支持类型：
  基础类型：String, Integer, Boolean, Long
  集合类型：String[], List<String>, Map<K,V>
  默认值：字段初始值即默认值

四大使用场景：
  功能开关 → A/B测试，灰度发布
  阈值参数化 → 限流、配额动态调整
  消息模板 → 文案改动不发版
  URL/接口管理 → 环境切换更方便

集中管理：
  所有 @MdpConfig 放在 LionService Bean 中
  统一入口，便于审查和管理
```

---

## 课后练习

1. **设计：** 为一个"员工激励系统"设计完整的 Lion 配置项，覆盖功能开关、奖励规则、通知文案，命名规范正确
2. **实践：** 实现一个配置变更监听器，当 `task.daily.limit` 变更时，清除相关 Redis 缓存
3. **思考：** 如果 Lion 服务不可用，服务会怎样？如何实现"Lion 宕机不影响业务"？

---

*← 上一课：Squirrel 分布式锁 | 下一课：综合实战 →*
