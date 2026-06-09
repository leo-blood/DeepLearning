# Lion — 美团内部配置中心

> 对标开源：Apollo / Nacos Config
> MDP 注解：`@MdpConfig`
> 项目封装：`LionService.java`（`ccsadmin-infrastructure/proxy/`）

---

## 第一节：为什么需要配置中心

### 1.1 静态配置文件的痛点

传统做法：把配置写在 `application.properties` 里。

```properties
# application.properties
incentive.task.page.size=200
cloud.office.apply.gray.mis=zhangsan,lisi
```

**问题**：
- 修改配置 → 重新打包 → 重新部署 → 服务重启中断
- 灰度发布困难：想对10%用户开启新功能，没法做到
- 配置分散：每个服务维护各自的配置文件，不好统一管理

### 1.2 配置中心解决了什么

```
Lion 控制台（美团内网）
    │
    │── 存储所有配置项 key-value
    │── 提供实时推送（配置变更秒级生效）
    │
    ↓
应用启动时拉取配置 + 监听变更推送
    ↓
@MdpConfig 字段自动更新（无需重启）
```

三大核心能力：
- **动态生效**：改配置不重启服务
- **环境隔离**：prod/staging/dev 各自独立配置
- **灰度控制**：同一个 key 可以对特定机器/百分比生效不同值

### 1.3 配置中心 vs 数据库 vs 代码常量

| 存放位置 | 适合的数据类型 | 典型例子 |
|---------|-------------|---------|
| 代码常量 | 永远不变的值 | `MAX_INT = 2147483647` |
| 数据库 | 用户/业务数据 | 任务信息、员工数据 |
| **Lion 配置中心** | 运营/运维可调节的值 | 开关、阈值、文案、URL |
| application.properties | 环境相关静态配置 | 端口号、数据库地址 |

---

## 第二节：@MdpConfig 注解详解

### 2.1 基本用法

```java
// LionService.java
@Component
@Data
public class LionService {

    // 格式：@MdpConfig("配置key:默认值")
    // Lion 控制台有配置时用控制台的值，没有时用默认值

    @MdpConfig("dx.token")
    public String DX_TOKEN;                      // 无默认值（必须在 Lion 配置）

    @MdpConfig("default.incentive.delay.minutes")
    public Integer incentiveDelayMinutes;        // Integer 类型，框架自动转换

    @MdpConfig("incentive.room.size")
    public Integer roomSize;

    @MdpConfig("default.es.staff.skill.amount")
    public Integer esStaffSkillAmount = 1000;    // 字段默认值（非 Lion 默认值）
}
```

### 2.2 注解中的默认值语法

```java
// 方式1：注解内指定默认值
@MdpConfig("incentive.room.size:50")            // Lion 未配置时默认 50
private Integer roomSize;

// 方式2：字段赋值（效果相同）
@MdpConfig("default.es.staff.skill.amount")
public Integer esStaffSkillAmount = 1000;       // Lion 未配置时为 1000

// 方式3：两者同时写，注解内优先级更高
@MdpConfig("some.key:200")
private Integer someValue = 100;                // 实际默认值是 200
```

### 2.3 支持的数据类型

```java
// 基本类型
@MdpConfig("flag.enabled")
private Boolean enabled;

@MdpConfig("page.size")
private Integer pageSize;

@MdpConfig("timeout.seconds")
private Long timeout;

// String 类型
@MdpConfig("error.message")
private String errorMessage;

// 数组类型（逗号分隔）
@MdpConfig("org.formal.skill.ids")
public String[] formalSkillIds;
// Lion 配置值："skill001,skill002,skill003"
// 注入结果：["skill001", "skill002", "skill003"]

// 复杂类型（JSON 格式）
@MdpConfig("ccs.metric.controller.condition")
public HashMap<Integer, Integer> metricControllerMap = Maps.newHashMap();
// Lion 配置值：{"1":7,"2":3,"3":10}
// 注入结果：{1→7, 2→3, 3→10}
```

---

## 第三节：动态配置实战

### 3.1 功能开关（灰度发布）

```java
// CloudOfficeStaffApplyService.java
@MdpConfig("CLOUD_OFFICE_APPLY_GRAY:ALL")     // 默认 "ALL" = 全量放开
private String CLOUD_OFFICE_APPLY_GRAY = "";
private static final String DEFAULT_GRAY_ALL = "ALL";

public void createApply(CloudOfficeStaffApplyRequestVO request) {
    // 根据 Lion 配置决定哪些员工可以申请
    if (!DEFAULT_GRAY_ALL.equals(CLOUD_OFFICE_APPLY_GRAY)) {
        // 灰度模式：只有配置名单内的员工可申请
        String[] allowedMis = CLOUD_OFFICE_APPLY_GRAY.split(",");
        if (!Arrays.asList(allowedMis).contains(currentUserMis)) {
            throw new BusiException("您暂无权限申请，请联系管理员");
        }
    }
    // 全量模式：所有人可申请
    doCreateApply(request);
}
```

灰度上线流程：
```
第一步：Lion 设置 CLOUD_OFFICE_APPLY_GRAY = "zhangsan,lisi"  → 仅这两人可用
第二步：验证没问题后，设置 = "ALL"                            → 全量上线
        如有问题，设置 = ""（空）                             → 全部关闭
```

### 3.2 消息文案动态配置

```java
// CloudOfficeStaffApplyService.java
// 消息模板从 Lion 实时拉取，运营可随时修改文案
@MdpConfig("CLOUD_OFFICE_APPLY_LEADER_SUCCESS:您管理的员工mis%s已报名混合办公...")
private String CLOUD_OFFICE_APPLY_LEADER_SUCCESS = "默认文案...";

@MdpConfig("CLOUD_OFFICE_APPLY_SUCCESS_STAFF_DAYS_BEFORE:2")
private Integer CLOUD_OFFICE_APPLY_SUCCESS_STAFF_DAYS_BEFORE = 2;

public void sendNotifyToLeader(String staffMis, String planDate) {
    // 直接用字段，Lion 推送更新后字段值自动变化
    String message = String.format(CLOUD_OFFICE_APPLY_LEADER_SUCCESS, staffMis, planDate, url);
    xmService.sendMessage(leaderMis, message);
}
```

### 3.3 阈值/参数动态调整

```java
// LionService.java
@MdpConfig("ccs.mis.message.size")
public Integer ccsMisMessageSize;

@MdpConfig("default.org.staff.amount")
public Integer defaultOrgStaffAmount;

// 使用场景：批量发消息时，每批次数量从 Lion 读取
// 线上发现批次太大导致超时 → 直接在 Lion 改小，秒级生效
```

---

## 第四节：LionService 设计模式

### 4.1 为什么要集中管理在 LionService

本项目把所有 Lion 配置集中在 `LionService.java`，而不是分散到各业务类：

```
✅ 集中管理的好处：
  1. 一处查看所有动态配置项
  2. 复用：多个 Service 注入同一个 LionService
  3. 统一命名规范（字段名和 Lion key 的对应关系清晰）

vs

散落在各业务类里：
  CloudOfficeStaffApplyService.java  里面直接用 @MdpConfig
  IncentiveTaskService.java          里面也直接用 @MdpConfig
  → 配置项分散，难以全局管理
```

但本项目也有直接在业务类里用的情况（`CloudOfficeStaffApplyService`），适用于"只有本类用到的配置"。

### 4.2 LionService 全部配置项速览

```java
// LionService.java — 项目所有 Lion 配置集中在此

// === 大象消息相关 ===
@MdpConfig("dx.token")
public String DX_TOKEN;                   // 大象 token

// === 激励任务相关 ===
@MdpConfig("default.incentive.delay.minutes")
public Integer incentiveDelayMinutes;     // 激励任务默认延迟分钟数

@MdpConfig("incentive.mock.notifyDate")
public String notifyMockDate;             // mock 通知日期（测试用）

@MdpConfig("incentive.ess.inprogress.url")
public String essInProgressUrl;           // ESS 进行中任务跳转链接

@MdpConfig("incentive.ess.expired.url")
public String essExpiredUrl;              // ESS 已过期任务跳转链接

@MdpConfig("ess.submit.class.url")
public String essSubmitClassUrl;          // ESS 抢班任务链接

@MdpConfig("incentive.room.size")
public Integer roomSize;                  // 激励任务房间数

// === 员工技能相关 ===
@MdpConfig("default.es.staff.skill.amount")
public Integer esStaffSkillAmount = 1000; // 每次查询技能的批量大小

@MdpConfig("org.formal.skill.ids")
public String[] formalSkillIds;           // 正式技能组ID列表

// === 组织架构相关 ===
@MdpConfig("default.org.staff.amount")
public Integer defaultOrgStaffAmount;     // 默认组织人数

@MdpConfig("org.default.paymentManagementMis.list")
public String[] defaultPaymentManagementMisList;  // 默认支付管理MIS列表

// === 消息相关 ===
@MdpConfig("ccs.mis.message.size")
public Integer ccsMisMessageSize;         // 批量消息大小

@MdpConfig("open.card.abstract.text")
public String openCardAbstractText = "云客服卡片通知消息";  // 卡片摘要

// === 指标管控相关 ===
@MdpConfig("ccs.metric.controller.condition")
public HashMap<Integer, Integer> metricControllerMap;  // 指标管控条件
```

### 4.3 直接在业务类使用的配置（CloudOfficeStaffApplyService）

```java
// 适合"只有本类需要"的配置，不放 LionService
@MdpConfig("CLOUD_OFFICE_APPLY_LEADER_SUCCESS:..默认文案..")
private String CLOUD_OFFICE_APPLY_LEADER_SUCCESS;   // 发给主管的消息

@MdpConfig("CLOUD_OFFICE_APPLY_SUCCESS_STAFF:..默认文案..")
private String CLOUD_OFFICE_APPLY_SUCCESS_STAFF;    // 发给员工的消息

@MdpConfig("CLOUD_OFFICE_APPLY_SUCCESS_STAFF_DAYS_BEFORE:2")
private Integer CLOUD_OFFICE_APPLY_SUCCESS_STAFF_DAYS_BEFORE;  // 提前天数

@MdpConfig("CLOUD_OFFICE_APPLY_GRAY:ALL")
private String CLOUD_OFFICE_APPLY_GRAY;             // 灰度控制
```

---

## 第五节：Lion 与静态配置的协作

### 5.1 两种配置的分工

本项目同时使用两套配置系统，各司其职：

```
application.properties（静态）：
  server.port=8080                    # 服务端口（启动时确定）
  management.server.port=8080         # 监控端口
  appkey=com.sankuai.csccratos.ccsadmin  # AppKey（不变的身份）
  cs.cli.enabled=true                 # UAC 功能开关（部署时确定）

Lion（@MdpConfig，动态）：
  dx.token                            # 大象 token（可能轮换）
  incentive.room.size                 # 房间数（运营可调）
  CLOUD_OFFICE_APPLY_GRAY             # 灰度名单（随时变）
  error.message.template              # 错误文案（随时改）
```

### 5.2 KMS 密钥与 Lion 配置的区别

```properties
# application.properties 里有 KMS 解密（启动时一次性解密）
cs.cli.uac-secret=$KMS{essuacSecret}     # 启动时解密，存在内存里

# ❌ 不应该把 token/secret 放 Lion（Lion 控制台可见，不够安全）
# ✅ 密钥放 KMS，动态配置放 Lion
```

### 5.3 @MdpConfig 的刷新机制

```
Lion 控制台修改配置值
    │── 推送变更通知给订阅的应用
    │
    ↓
应用接收到推送
    │── MDP 框架扫描 @MdpConfig 字段
    │── 更新字段值（反射赋值）
    ↓
下一次方法调用时，字段已是新值（无需重启）
```

**注意**：字段更新不是原子的，在极端并发情况下可能读到旧值和新值的混合。对于配置开关类场景，这通常可以接受；对于强一致性场景，需要额外保护。

### 5.4 调试与排查指南

**问题1：@MdpConfig 字段始终是默认值**
- 检查 Lion 控制台该 key 是否存在
- 检查配置的 AppKey 是否正确（`appkey = com.sankuai.csccratos.ccsadmin`）
- 检查是否在 prod/staging 对应环境下配置

**问题2：修改了 Lion 配置但没有生效**
- 等待 5-10 秒（推送有延迟）
- 检查应用是否正在运行并连接 Lion
- 检查字段是否确实有 `@MdpConfig` 注解

**问题3：复杂类型（HashMap/数组）解析失败**
- 数组：确保 Lion 配置值用逗号分隔，无空格
- HashMap：确保是合法 JSON，如 `{"1":7,"2":3}`

### 5.5 最佳实践总结

| 原则 | 说明 |
|------|------|
| 功能开关必须走 Lion | 不能硬编码 `if (false)` 来关功能 |
| 阈值/批量大小走 Lion | 方便线上调参，不重启 |
| 密钥走 KMS，不走 Lion | Lion 控制台权限较宽松 |
| 默认值必须合理 | Lion 挂了或 key 不存在时，默认值要能保证系统正常运转 |
| 集中管理优先 | 优先放 LionService，只有"本类独用"的配置才放业务类 |
