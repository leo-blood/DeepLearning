# 课件 06｜Crane 分布式定时任务

---

## 课程目标

学完本课件后，你能够：

1. 解释分布式定时任务与 `@Scheduled` 的根本区别
2. 使用 `@Crane` 注解编写标准的分布式任务
3. 掌握幂等设计、批量分页、故障补偿三大模式
4. 使用控制台进行手动触发、暂停、查看历史

---

## 一、为什么不能用 @Scheduled？

### 1.1 问题重现

```java
// 看似简单的定时任务
@Scheduled(cron = "0 2 * * * ?")  // 每天凌晨 2 点
public void syncEmployeeData() {
    List<Employee> employees = employeeService.findAll();
    for (Employee e : employees) {
        syncToExternalSystem(e);
    }
}
```

```
部署 3 台实例：

实例 A:  ┌─── 02:00 执行 syncEmployeeData ───┐
实例 B:  ┌─── 02:00 执行 syncEmployeeData ───┐  ← 同时触发！
实例 C:  ┌─── 02:00 执行 syncEmployeeData ───┐

结果：同一份员工数据被同步了 3 次！
```

### 1.2 @Scheduled vs Crane 对比

| 特性 | @Scheduled | Crane |
|------|-----------|-------|
| 多实例执行 | 每台都执行 ❌ | 只有一台执行 ✅ |
| 可视化管理 | 无 ❌ | 控制台查看 ✅ |
| 手动触发 | 不支持 ❌ | 支持 ✅ |
| 执行历史 | 无 ❌ | 记录每次执行 ✅ |
| 参数传递 | 不支持 ❌ | 支持 ✅ |
| 失败告警 | 需自己实现 | 内置 ✅ |

---

## 二、Crane 基础使用

### 2.1 注解说明

```java
@Crane(
    name = "sync-employee-data",        // 任务唯一标识（建议 kebab-case）
    cron = "0 0 2 * * ?",              // Cron 表达式（每天 02:00）
    description = "每日同步员工数据到外部系统",
    timeout = 300,                       // 最长执行时间（秒），超时强制结束
    retryCount = 3                       // 失败后重试次数
)
public void syncEmployeeData() {
    // 任务逻辑
}
```

### 2.2 Cron 表达式速查

```
格式：秒 分 时 日 月 周

常用示例：
  0 0 2 * * ?      每天凌晨 02:00
  0 */5 * * * ?    每 5 分钟
  0 0 9 ? * MON-FRI  工作日早 9 点
  0 30 8 1 * ?     每月 1 日 08:30
  0 0 0 1 1 ?      每年 1 月 1 日 00:00

字段范围：
  秒: 0-59
  分: 0-59
  时: 0-23
  日: 1-31
  月: 1-12 或 JAN-DEC
  周: 0-7 或 SUN-SAT（0和7都表示周日）
  ? 表示不指定（日和周不能同时指定，用 ? 占位）
```

### 2.3 标准任务模板

```java
@Component
@Slf4j
public class EmployeeSyncJob {

    @Autowired
    private EmployeeRepository employeeRepository;

    @Crane(
        name = "employee-daily-sync",
        cron = "0 0 2 * * ?",
        description = "每日同步员工数据",
        timeout = 600
    )
    public void run() {
        log.info("[EmployeeSyncJob] 任务开始");
        long start = System.currentTimeMillis();

        try {
            int processed = doSync();
            long cost = System.currentTimeMillis() - start;
            log.info("[EmployeeSyncJob] 任务完成, processed={}, costMs={}", processed, cost);
        } catch (Exception e) {
            log.error("[EmployeeSyncJob] 任务失败", e);
            throw e;  // 重新抛出，让 Crane 知道任务失败（触发告警/重试）
        }
    }

    private int doSync() {
        // 实际业务逻辑
        return 0;
    }
}
```

---

## 三、批量分页处理

### 3.1 为什么要分页？

```
反模式：一次查全部
  List<Employee> all = employeeRepository.findAll();  // 10万条！
  → OOM
  → DB 慢查询
  → GC 停顿

正确：分页处理
  分批查询 → 处理 → 再查下一批
  每批 1000 条，内存可控
```

### 3.2 游标分页模板

```java
@Crane(name = "task-settlement", cron = "0 0 1 * * ?")
public void runSettlement() {
    final int BATCH_SIZE = 1000;
    Long lastId = 0L;  // 游标：上次处理的最大 ID
    int totalProcessed = 0;

    while (true) {
        // 每次查 BATCH_SIZE + 1 条，用于判断是否还有数据
        List<Task> tasks = taskRepository.findByIdGreaterThan(lastId, BATCH_SIZE);

        if (CollectionUtils.isEmpty(tasks)) {
            break;  // 没有更多数据，结束
        }

        // 处理这批数据
        for (Task task : tasks) {
            try {
                settleTask(task);
                totalProcessed++;
            } catch (Exception e) {
                log.error("Settlement failed for taskId={}", task.getId(), e);
                // 单条失败，记录并继续（不影响整批）
            }
        }

        // 更新游标
        lastId = tasks.get(tasks.size() - 1).getId();

        log.info("Batch done, lastId={}, batchSize={}", lastId, tasks.size());

        // 如果这批不足 BATCH_SIZE，说明是最后一批
        if (tasks.size() < BATCH_SIZE) {
            break;
        }
    }

    log.info("Settlement completed, totalProcessed={}", totalProcessed);
}
```

### 3.3 时间窗口分页

```java
@Crane(name = "order-stats", cron = "0 5 0 * * ?")  // 每天 00:05 统计昨天数据
public void buildDailyStats() {
    LocalDate yesterday = LocalDate.now().minusDays(1);
    LocalDateTime start = yesterday.atStartOfDay();
    LocalDateTime end = yesterday.atTime(23, 59, 59);

    // 按时间窗口查询，避免全表扫描
    List<Order> orders = orderRepository.findByCreatedAtBetween(start, end);
    // 统计处理...
}
```

---

## 四、幂等设计

### 4.1 任务幂等性

```java
@Crane(name = "daily-reward-grant", cron = "0 0 10 * * ?")
public void grantDailyReward() {
    String today = LocalDate.now().toString();  // "2024-01-15"

    // 检查今天是否已经执行过（避免重复发放）
    if (jobLogRepository.existsByJobNameAndDate("daily-reward-grant", today)) {
        log.info("已执行过，跳过: date={}", today);
        return;
    }

    try {
        // 执行业务逻辑
        int granted = rewardService.grantDailyRewards();
        log.info("发放完成: count={}", granted);

        // 记录执行日志（幂等标记）
        jobLogRepository.save(JobLog.success("daily-reward-grant", today));
    } catch (Exception e) {
        log.error("发放失败", e);
        throw e;
    }
}
```

### 4.2 状态机防重

```java
// 使用状态机 + 条件更新，天然幂等
@Transactional
public boolean settleTask(Long taskId) {
    // 只处理 PENDING 状态的任务，已处理的自动跳过
    int updated = taskRepository.updateStatusIfPending(taskId, TaskStatus.PROCESSING);
    if (updated == 0) {
        // CAS 失败：任务已被处理或不是 PENDING 状态
        return false;
    }

    // 执行结算逻辑
    doActualSettlement(taskId);

    taskRepository.updateStatus(taskId, TaskStatus.SETTLED);
    return true;
}

// SQL: UPDATE task SET status='PROCESSING' WHERE id=? AND status='PENDING'
```

---

## 五、故障补偿模式

### 5.1 场景

```
任务运行到一半，服务器宕机：
  已处理：任务 1~5000
  未处理：任务 5001~10000

重启后重新执行：
  从头开始 → 任务 1~5000 被重复处理！
```

### 5.2 断点续跑

```java
@Crane(name = "data-migration", cron = "0 0 3 * * ?")
public void migrateData() {
    // 获取上次的断点（上次处理到的最大 ID）
    Long checkpoint = migrationCheckpoint.get("data-migration");
    Long lastProcessedId = checkpoint != null ? checkpoint : 0L;

    log.info("从断点开始: lastProcessedId={}", lastProcessedId);

    Long currentId = lastProcessedId;
    while (true) {
        List<LegacyData> batch = legacyRepo.findByIdGreaterThan(currentId, 500);
        if (batch.isEmpty()) break;

        for (LegacyData data : batch) {
            migrate(data);
            currentId = data.getId();
        }

        // 定期保存断点（每批处理后）
        migrationCheckpoint.save("data-migration", currentId);
        log.info("断点更新: currentId={}", currentId);
    }
}
```

---

## 六、控制台操作指南

| 操作 | 场景 | 注意事项 |
|------|------|---------|
| **手动触发** | 紧急补跑、测试 | 确认任务幂等，避免重复处理 |
| **暂停任务** | 发现 Bug 临时停用 | 暂停不影响正在执行的实例 |
| **查看执行历史** | 排查任务是否执行 | 可查参数、执行时间、耗时 |
| **修改 Cron** | 调整执行时间 | 线上修改，下次生效 |
| **查看日志** | 排查执行失败原因 | 结合任务日志和应用日志 |

---

## 七、核心知识点总结

```
Crane vs @Scheduled：
  分布式环境下只有一台执行
  有控制台可视化管理
  支持手动触发、历史记录

@Crane 注解关键字段：
  name  → 任务唯一标识
  cron  → 执行时间（标准 6 字段 Cron）
  timeout → 超时保护

三大设计模式：
  批量分页 → 游标翻页，避免 OOM
  幂等设计 → 状态机 CAS 更新
  断点续跑 → 保存 checkpoint，支持重启恢复

失败处理：
  单条失败 → 记录日志，继续处理其他
  整体失败 → 抛异常，Crane 记录失败状态
  重试 → 配置 retryCount，自动重试
```

---

## 课后练习

1. **基础：** 写一个 Cron 表达式，实现"每月最后一天 23:55 执行"
2. **设计：** 如果任务处理 10 万条数据，每条 10ms，执行需要 1000 秒。如何设计分片并行执行？
3. **排查：** 任务显示"执行成功"但数据没有处理，可能是什么原因？如何通过代码和日志定位？

---

*← 上一课：Mafka Consumer | 下一课：Squirrel 分布式缓存基础 →*
