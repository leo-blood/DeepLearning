# 课件 05｜Mafka Consumer：消费者设计与幂等性

---

## 课程目标

学完本课件后，你能够：

1. 实现一个标准的 Consumer 监听器并处理返回状态
2. 解释幂等性的定义及三种实现方案
3. 理解消费失败重试机制和死信队列
4. 设计"自消费"模式解决事务一致性问题

---

## 一、Consumer 基础实现

### 1.1 标准消费者模板

```java
@Component
@Slf4j
public class TaskCompletedEventConsumer {

    @Autowired
    private TaskService taskService;

    // @MafkaListener 标注消费者
    @MafkaListener(
            topic = "ccs_task_completed",           // 监听的 Topic
            consumerGroup = "ccs_admin_task_group", // ConsumerGroup（同组负载均衡）
            bgNameSpace = "ccs_admin"               // 业务命名空间
    )
    public ConsumeStatus onMessage(MessageExt message) {
        String body = message.getBody();
        log.info("Received message: msgId={}, body={}", message.getMsgId(), body);

        try {
            // 1. 反序列化
            TaskCompletedEvent event = JSON.parseObject(body, TaskCompletedEvent.class);

            // 2. 幂等检查（核心！）
            if (taskService.isAlreadyProcessed(event.getEventId())) {
                log.info("Duplicate message, skip. eventId={}", event.getEventId());
                return ConsumeStatus.CONSUME_SUCCESS;  // 幂等：重复消息也返回成功
            }

            // 3. 业务处理
            taskService.handleTaskCompleted(event);

            // 4. 成功
            return ConsumeStatus.CONSUME_SUCCESS;

        } catch (BusinessException e) {
            // 业务异常：记录日志，不重试（数据问题重试也没用）
            log.error("Business error processing message: msgId={}, error={}",
                    message.getMsgId(), e.getMessage());
            return ConsumeStatus.CONSUME_SUCCESS;  // 返回 SUCCESS 避免无限重试

        } catch (Exception e) {
            // 系统异常：重试（可能是临时网络/DB 问题）
            log.error("System error, will retry: msgId={}", message.getMsgId(), e);
            return ConsumeStatus.RECONSUME_LATER;  // 告诉 Mafka 稍后重试
        }
    }
}
```

### 1.2 ConsumeStatus 说明

| 返回值 | 含义 | 触发场景 |
|-------|------|---------|
| `CONSUME_SUCCESS` | 消费成功，提交 offset | 处理成功 **或** 业务异常（不重试） |
| `RECONSUME_LATER` | 消费失败，稍后重试 | 系统异常（DB宕机、网络抖动等） |

**关键原则：** 业务逻辑错误返回 `CONSUME_SUCCESS`，系统错误才返回 `RECONSUME_LATER`。

---

## 二、幂等性设计

### 2.1 为什么要幂等？

```
At Least Once 投递的问题：
  消费者处理完 → 还没提交 offset → 宕机重启
  Mafka 重新投递同一条消息
  ─────────────────────────────────────────
  同一条消息被消费 2 次！
  → 积分发了 2 次
  → 通知发了 2 次
  → 数据统计翻倍
```

**幂等性定义：** 同一操作执行多次与执行一次的结果相同。

### 2.2 方案一：唯一键数据库约束

```java
// 消息处理表，以 eventId 为唯一键
CREATE TABLE message_process_record (
    id BIGINT AUTO_INCREMENT,
    event_id VARCHAR(64) NOT NULL UNIQUE,  -- 唯一约束
    topic VARCHAR(128),
    created_at DATETIME,
    PRIMARY KEY (id)
);

// 消费时插入记录，利用数据库唯一约束保幂等
public void processWithDbIdempotency(TaskCompletedEvent event) {
    try {
        // 插入消费记录（如果已存在会抛 DuplicateKeyException）
        messageRecordRepository.insert(
            MessageProcessRecord.of(event.getEventId(), "ccs_task_completed")
        );
        // 插入成功 → 第一次消费 → 执行业务
        doBusinessLogic(event);
    } catch (DuplicateKeyException e) {
        // 插入失败 → 重复消费 → 跳过
        log.info("Duplicate message ignored: eventId={}", event.getEventId());
    }
}
```

**优点：** 强一致，简单可靠  
**缺点：** 每次消费都写 DB，高并发时 DB 压力大

### 2.3 方案二：Redis SET NX 幂等

```java
@Autowired
private StringRedisTemplate redisTemplate;

public boolean tryProcessOnce(String eventId) {
    String key = "mafka:processed:" + eventId;
    // SET key value NX EX 7d（7天内不重复处理）
    Boolean success = redisTemplate.opsForValue()
            .setIfAbsent(key, "1", Duration.ofDays(7));
    return Boolean.TRUE.equals(success);
}

// 使用
public void onMessage(TaskCompletedEvent event) {
    if (!tryProcessOnce(event.getEventId())) {
        log.info("Duplicate, skip: eventId={}", event.getEventId());
        return;
    }
    doBusinessLogic(event);
}
```

**优点：** 性能好（Redis 操作 < 1ms）  
**缺点：** Redis 故障时可能重复处理

### 2.4 方案三：业务天然幂等

```java
// 最优雅：业务逻辑本身就是幂等的
// 例：UPDATE 语态（而非 INSERT）

// 发放积分：用 UPDATE 代替 INSERT，天然幂等
public void grantReward(Long taskId, String staffMis, Integer points) {
    // 使用 INSERT IGNORE 或 REPLACE INTO
    // 或使用 UPDATE ... WHERE status != 'GRANTED' 的条件更新
    int affected = rewardRepository.updateIfNotGranted(taskId, points);
    if (affected == 0) {
        log.info("Reward already granted for taskId={}", taskId);
    }
}
```

**适用场景：** 状态机更新、计数器操作（`SET` 而非 `INCREMENT`）

---

## 三、消费失败与重试

### 3.1 重试机制

```
Mafka 重试策略（RECONSUME_LATER）：

第1次失败 → 10秒后重试
第2次失败 → 30秒后重试
第3次失败 → 1分钟后重试
...
第16次失败 → 2小时后重试
第17次失败 → 进入死信队列（Dead Letter Queue）
```

### 3.2 死信队列处理

```java
// 死信 Topic 命名规范：原 Topic + "_DEAD_LETTER" 或 "%DLQ%_consumerGroup"
@MafkaListener(
    topic = "%DLQ%ccs_admin_task_group",
    consumerGroup = "ccs_admin_dlq_group"
)
public ConsumeStatus handleDeadLetter(MessageExt message) {
    // 死信消息：写入告警 + 人工处理队列
    alertService.sendAlert("死信消息", message.getBody());
    deadLetterRepository.save(message);
    return ConsumeStatus.CONSUME_SUCCESS;
}
```

---

## 四、自消费模式（事务一致性）

### 4.1 问题场景

```
需求：任务完成 → 更新DB + 发消息，必须保证原子性

问题：
  方案A：先更新DB，再发消息
         DB成功，发消息失败 → DB有记录，但没有消息 ← 不一致
  
  方案B：先发消息，再更新DB
         消息发出，DB更新失败 → 消息有，DB无 ← 不一致

如何保证两者原子？
```

### 4.2 自消费（Self-Consuming）方案

```
核心思路：把"发消息"变成"写DB"，然后用定时任务/触发器发消息

┌─────────────────────────────────────────────────────────────┐
│  同一数据库事务中：                                           │
│   1. 更新业务表（task_status = COMPLETED）                   │
│   2. 写消息记录表（message_outbox 状态 = PENDING）           │
│                          ↓                                   │
│         CRANE 定时任务（每5秒）                               │
│         扫描 PENDING 消息 → 发 Mafka → 更新状态 = SENT      │
│                          ↓                                   │
│         Mafka 投递给下游消费者                                │
└─────────────────────────────────────────────────────────────┘
```

```java
// 步骤1：事务内写 outbox
@Transactional
public void completeTask(Long taskId) {
    // 更新业务状态
    taskRepository.updateStatus(taskId, TaskStatus.COMPLETED);

    // 写消息到 outbox 表（同一事务，原子）
    MessageOutbox outbox = MessageOutbox.builder()
            .topic("ccs_task_completed")
            .body(JSON.toJSONString(new TaskCompletedEvent(taskId)))
            .status(OutboxStatus.PENDING)
            .build();
    messageOutboxRepository.save(outbox);
}

// 步骤2：Crane 定时发送 outbox 消息
@Crane(cron = "*/5 * * * * ?")
public void flushOutboxMessages() {
    List<MessageOutbox> pending = messageOutboxRepository.findPending(100);
    for (MessageOutbox msg : pending) {
        try {
            mafkaClient.sendMessage(msg.getTopic(), msg.getBody());
            messageOutboxRepository.updateStatus(msg.getId(), OutboxStatus.SENT);
        } catch (Exception e) {
            log.error("Failed to flush outbox message: id={}", msg.getId(), e);
            // 下次定时任务继续重试
        }
    }
}
```

---

## 五、消费者监控指标

| 指标 | 含义 | 关注点 |
|------|------|-------|
| **消费延迟（Lag）** | 未消费消息数 | Lag 持续增大 → 消费者处理能力不足 |
| **消费 TPS** | 每秒处理消息数 | 与生产 TPS 对比，判断是否能追上 |
| **失败率** | RECONSUME_LATER 比例 | > 1% 需排查 |
| **死信数量** | 进入 DLQ 的消息数 | > 0 需立即人工处理 |

---

## 六、核心知识点总结

```
Consumer 实现：
  @MafkaListener 标注方法
  返回 CONSUME_SUCCESS / RECONSUME_LATER
  业务异常 → SUCCESS（不重试）
  系统异常 → RECONSUME_LATER（重试）

幂等性三方案：
  DB 唯一键 → 强一致，有 DB 压力
  Redis SETNX → 性能好，依赖 Redis
  业务天然幂等 → 最优（UPDATE 而非 INSERT）

事务一致性：
  Outbox 模式 → DB + 消息原子写入
  异步发送 → 最终一致性

消费失败处理：
  重试 16 次 → 死信队列
  死信队列 → 告警 + 人工处理
```

---

## 课后练习

1. **实践：** 实现一个消费者，处理"员工转岗"事件，同时需要更新3张表，如何设计幂等？
2. **设计：** 如果消费者平均处理时间 500ms，生产者每秒发送 1000 条消息，需要几个消费者实例？
3. **思考：** Outbox 模式和两阶段提交（2PC）相比有什么优劣？

---

*← 上一课：Mafka Producer | 下一课：Crane 分布式定时任务 →*
