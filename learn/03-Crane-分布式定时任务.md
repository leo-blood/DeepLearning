# Crane — 美团内部分布式定时任务

> 对标开源：xxl-job / Quartz
> 项目中代码：`ccsadmin-starter/src/main/java/.../job/`
> 核心注解：`com.cip.crane.client.spring.annotation.Crane`

---

## 第一节：为什么需要分布式定时任务

### 1.1 传统 @Scheduled 的问题

Spring 内置的 `@Scheduled` 在单机下没问题，但微服务多实例部署时：

```
机器A  ── @Scheduled ──▶ 每天0点执行 calcStaffLevel()
机器B  ── @Scheduled ──▶ 每天0点执行 calcStaffLevel()   ← 重复执行！
机器C  ── @Scheduled ──▶ 每天0点执行 calcStaffLevel()   ← 重复执行！
```

后果：员工等级被计算3次，数据错乱。

### 1.2 分布式定时任务解决了什么

```
Crane 控制台
    │── 0点触发 "ccsStaffLevelJob" ──▶ 从注册的3台机器中选1台
                                              │
                                         机器A 执行
                                         机器B 空闲
                                         机器C 空闲
```

Crane 保证：
- **只有一台机器执行**（分布式选主）
- **执行记录可追溯**（控制台看历史）
- **动态修改执行时间**（不重启服务）
- **手动触发**（测试/补偿场景）
- **传参数**（控制台下发执行参数）

### 1.3 Crane 在项目中的规模

本项目共有 **25+ 个 Crane 任务**，覆盖：

| 业务域 | 任务数 | 代表任务 |
|--------|--------|---------|
| 激励任务 | 5 | 状态流转、奖惩执行 |
| 成长体系 | 4 | 员工等级计算、等级刷新 |
| 云办公 | 5 | 云桌面分配、标签更新 |
| 组织架构 | 2 | 日常对账、数据初始化 |
| 招募培训 | 3 | 任务状态更新 |
| 其他 | 6+ | 智管家日报、消息投递等 |

---

## 第二节：Crane 的核心机制

### 2.1 任务注册原理

```
应用启动
    │── Crane Client 扫描 @Crane 注解
    │── 将 {taskName, appKey, ip, method} 上报给 Crane 服务端
    │── Crane 控制台展示可调度的任务列表

触发时
    │── Crane 服务端在控制台配置的 cron 时间到达
    │── 选择一台健康机器（轮询/随机）
    │── 通过 HTTP/RPC 调用该机器上的任务方法
    │── 记录执行结果、耗时
```

### 2.2 执行参数传递

Crane 支持两种参数传递方式：

```
方式1：控制台配置固定参数（String 类型）
  控制台设置：taskParam = "2024-01-01"
  方法接收：void myJob(String param)

方式2：无参数
  方法签名：void myJob()
```

本项目大多数 Job 接收 `String dateStr` 参数，由控制台传入日期字符串，方便补跑历史数据。

---

## 第三节：@Crane 注解使用

### 3.1 最简示例

```java
// TaskRewardPenaltyJob.java
import com.cip.crane.client.spring.annotation.Crane;

@Service
@Slf4j
public class TaskRewardPenaltyJob {

    @Autowired
    private CcsTaskRewardService ccsTaskRewardService;

    // @Crane 的值就是在 Crane 控制台里注册的任务名
    @Crane("taskRewardPenaltyJob")
    public void taskRewardPenaltyJob(String dateStr) {
        log.info("任务奖惩Job开始, dateStr:{}", dateStr);
        try {
            // 业务逻辑
            ccsTaskRewardService.processRewardPenalty(dateStr);
        } catch (Exception e) {
            log.error("任务奖惩Job异常", e);
        }
    }
}
```

### 3.2 多任务在同一 Job 类中

```java
// IncentiveTaskJob.java
@Service
@Slf4j
public class IncentiveTaskJob {

    // 一个 Job 类可以注册多个 Crane 任务
    @Crane("incentiveStatusUpdate")      // 任务1：状态流转
    public void updateTaskStatus(String dateStr) {
        log.info("激励任务状态流转开始");
        // 待下发 → 已下发 → 考核整改中 → ...
    }

    @Crane("doIncentiveTask")            // 任务2：执行圈人/推送
    public void doIncentiveTask(String dateStr) {
        log.info("激励任务执行开始");
        // 圈人、发消息
    }
}
```

### 3.3 云办公多任务示例

```java
// CloudOfficeStaffDesktopJob.java
@Service
public class CloudOfficeStaffDesktopJob {

    @Crane("cloudOfficeStaffDesktopJob")       // 主任务
    public void cloudOfficeStaffDesktopJob(String param) { ... }

    @Crane("cloudOfficeStaffDesktopCreateJob") // 账号创建
    public void createDesktopAccount(String param) { ... }

    @Crane("cloudOfficeStaffDesktopInitJob")   // 初始化
    public void initDesktop(String param) { ... }

    @Crane("cloudOfficeStaffDesktopRemindJob") // 到期提醒
    public void remindDesktopExpiry(String param) { ... }
}
```

---

## 第四节：Crane 控制台操作

### 4.1 控制台核心功能

在 Crane 控制台（内网地址）可以：

```
1. 查看任务列表
   taskName         执行时间        上次执行结果    机器
   taskRewardPenaltyJob  每天23:00  SUCCESS        10.0.1.23
   ccsStaffLevelJob      每天01:00  SUCCESS        10.0.1.24

2. 修改 cron 表达式
   原来：0 0 23 * * ?（每天23:00）
   改为：0 30 22 * * ?（每天22:30）
   → 不需要重启应用生效

3. 手动触发
   点击"立即执行" → 传入参数 → 选择机器 → 执行
   常用于：测试、补跑某天的数据

4. 查看执行历史
   时间、耗时、机器IP、执行参数、是否成功
```

### 4.2 Cron 表达式格式

Crane 使用标准 Quartz Cron（6或7位）：

```
秒 分 时 日 月 周 [年]

常用示例：
  0 0 1 * * ?      每天凌晨1点
  0 0 23 * * ?     每天晚上23点
  0 30 8 * * 1-5   工作日早上8:30
  0 0/30 * * * ?   每30分钟
  0 0 0 1 * ?      每月1日0点
```

---

## 第五节：最佳实践与本项目核心 Job 分析

### 5.1 Job 编写规范

**规范1：异常必须捕获，不能抛出**

```java
// ✅ 正确：捕获异常，记录日志，任务继续
@Crane("myJob")
public void myJob(String dateStr) {
    try {
        doSomething();
    } catch (Exception e) {
        log.error("myJob 执行失败, dateStr:{}", dateStr, e);
        // 不要 throw！抛出会让 Crane 认为任务失败并可能重试
    }
}

// ❌ 错误：异常上抛，影响 Crane 调度判断
@Crane("myJob")
public void myJob(String dateStr) throws Exception {
    doSomething(); // 抛出 Exception
}
```

**规范2：幂等性设计**

```java
@Crane("ccsStaffLevelJob")
public void calcStaffLevel(String dateStr) {
    // 根据日期查询是否已计算
    boolean alreadyCalculated = staffLevelService.checkCalculated(dateStr);
    if (alreadyCalculated) {
        log.info("{}等级已计算，跳过", dateStr);
        return;
    }
    // 计算逻辑
    staffLevelService.calcStaffLevel(dateStr);
}
```

**规范3：大批量数据分页处理**

```java
@Crane("ccsStaffLevelJob")
public void calcStaffLevel(String dateStr) {
    int pageSize = 200;
    int pageNum = 1;
    while (true) {
        // 分页查询，每次 200 条，避免 OOM
        List<Staff> staffList = staffMapper.queryByPage(pageNum, pageSize);
        if (CollectionUtils.isEmpty(staffList)) break;

        staffList.forEach(staff -> processStaff(staff));
        pageNum++;
    }
}
```

**规范4：记录执行日志，便于追踪**

```java
@Crane("taskRewardPenaltyJob")
public void taskRewardPenaltyJob(String dateStr) {
    long startTime = System.currentTimeMillis();
    log.info("[taskRewardPenaltyJob] 开始执行, dateStr:{}", dateStr);
    int processCount = 0;
    try {
        processCount = doProcess(dateStr);
    } catch (Exception e) {
        log.error("[taskRewardPenaltyJob] 执行异常, dateStr:{}", dateStr, e);
    } finally {
        log.info("[taskRewardPenaltyJob] 执行完毕, dateStr:{}, 处理条数:{}, 耗时:{}ms",
            dateStr, processCount, System.currentTimeMillis() - startTime);
    }
}
```

### 5.2 核心 Job 详解：激励任务状态流转

```java
// IncentiveTaskJob.java — 项目最核心的 Job

@Crane("incentiveStatusUpdate")
public void updateTaskStatus(String dateStr) {
    // 状态流转规则（禁止在 HTTP 接口中修改状态！）：
    //
    // 待下发  ──（开始时间到达）──▶ 已下发
    // 已下发  ──（结束时间到达）──▶ 考核整改中
    // 考核整改中 ──（整改期结束）──▶ 待管控
    // 待管控  ──（管控期开始）──▶ 待处罚
    // 待处罚  ──（处罚生效）──▶ 处罚生效中
    // 处罚生效中 ──（失效日期）──▶ 已结束

    Date now = new Date(dateStr);

    // 1. 待下发 → 已下发
    List<IncentiveTask> toIssued = incentiveMapperService
        .queryByStatusAndStartDate(TaskStatusEnum.TO_BE_ISSUED, now);
    toIssued.forEach(task -> updateStatus(task, TaskStatusEnum.ISSUED));

    // 2. 已下发 → 考核整改中
    // ...以此类推
}
```

**为何禁止 API 修改状态**：如果 HTTP 接口也能改状态，会与 Job 产生竞争，导致状态跳跃（如从"已下发"直接跳到"已结束"）。

### 5.3 本项目 Crane 任务总览

```
激励任务相关：
  incentiveStatusUpdate       每天定时   激励任务状态流转（7个状态节点）
  doIncentiveTask             每天定时   执行圈人/推送操作
  taskRewardPenaltyJob        每天23点   判断任务完成，决定奖惩
  ccsTaskCancelPenaltyJob     定时       取消惩罚
  doExpirePenalty             定时       处罚到期处理

成长体系相关：
  ccsStaffLevelJob            每天01点   员工等级计算（批量，性能敏感！）
  ccsStaffLevelNewPeopleJob   定时       新人等级计算
  ccsStaffLevelRefreshIdJob   定时       等级 ID 刷新
  RuleReminderJob             每天01点   等级规则变更提醒（T-7/T-3/T-1）

云办公相关：
  cloudOfficeStaffDesktopJob       定时   云桌面分配主任务
  cloudOfficeStaffDesktopCreateJob 定时   云桌面账号创建
  cloudOfficeStaffDesktopInitJob   定时   云桌面初始化
  cloudOfficeStaffDesktopRemindJob 定时   到期提醒
  cloudOfficeStaffLabelUpdateJob   定时   员工标签更新
  cloudOfficeStaffLabelNoticeJob   定时   权益通知

组织架构相关：
  ccsOrgDailyReconciliationTask    每天   日常对账
  ccsOrgDataInitializationTask     定时   数据初始化

其他：
  RecruitmentTaskStatusUpdateJob           招募任务状态更新
  RecruitmentTaskFinishedStatusUpdateJob   招募结束状态
  NewRecruitmentTaskSubscribeUserStatJob   订阅用户统计
  SmartButlerDailySummaryJob               智管家日报
  MessageDeliveryTaskStatusUpdateJob       消息投递状态
  taskCancelStaffClassInfoJob              取消班次提醒
  getCustomerGrowthEvent                   获取客户成长事件
```
