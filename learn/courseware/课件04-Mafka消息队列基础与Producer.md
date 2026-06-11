# 课件 04｜Mafka 消息队列：异步解耦与 Producer 设计

---

## 课程目标

学完本课件后，你能够：

1. 解释消息队列的三大价值：异步、解耦、削峰
2. 说清楚 Topic、bgNameSpace、ConsumerGroup 的关系
3. 正确配置并使用 Producer 发送普通消息和延迟消息
4. 理解"至少一次投递"语义及其对业务的影响

---

## 一、为什么需要消息队列？

### 1.1 同步调用的问题

```
场景：员工完成任务后，需要：
  1. 更新任务状态
  2. 发放积分
  3. 推送通知
  4. 更新数据看板
  5. 同步 CRM 系统

同步调用方式：
  员工操作 → Service A 串行调用 B、C、D、E、F
              ↓
  总耗时 = B + C + D + E + F = 1000ms+
  任何一个失败 → 整个流程回滚
  高峰期 → 所有服务同时压力暴增
```

### 1.2 消息队列的三大价值

```
异步（Async）：
  员工操作 → 发消息(1ms) → 立即返回
                 ↓
         各服务异步消费（不阻塞用户）

解耦（Decouple）：
  Service A 不需要知道 B、C、D 的存在
  新增 E 服务时，A 无需改代码，直接订阅 Topic

削峰（Peak Shaving）：
  高峰期 10000 消息/秒
  消费速度 1000 消息/秒
  ─────────────────────
  消息队列作缓冲，消费者按能力处理，不被压垮
```

### 1.3 Mafka 是什么？

Mafka = 美团版 Kafka 封装

```
原生 Kafka API（复杂）           Mafka（简化封装）
  Properties props = new...      @Autowired MafkaClient
  KafkaProducer producer = ...   client.sendMessage(topic, msg)
  ProducerRecord record = ...    → 完成
  producer.send(record)
  → 10+ 行配置代码
```

---

## 二、核心概念

### 2.1 三层结构

```
┌─────────────────────────────────────────────────────┐
│  bgNameSpace（业务隔离层）                            │
│  例：ccs_admin                                       │
│                                                      │
│  ┌──────────────────────────────────────────────┐   │
│  │  Topic（消息主题）                             │   │
│  │  例：task_completed                           │   │
│  │                                               │   │
│  │  Producer → [消息] → [消息] → [消息] →        │   │
│  │                                               │   │
│  │  ConsumerGroup A ──── 消费所有消息             │   │
│  │  ConsumerGroup B ──── 消费所有消息（独立）      │   │
│  └──────────────────────────────────────────────┘   │
└─────────────────────────────────────────────────────┘
```

| 概念 | 类比 | 说明 |
|------|------|------|
| **bgNameSpace** | 部门 | 业务隔离，不同业务不串消息 |
| **Topic** | 公告板 | 一类消息的集合 |
| **ConsumerGroup** | 订阅者 | 同一组内负载均衡，不同组各收一份 |
| **Partition** | 公告板分格 | 并发处理，同组内负载均衡 |

### 2.2 消息投递语义

```
Mafka 保证：At Least Once（至少一次）

含义：
  ✅ 消息一定会投递（不丢失）
  ⚠️ 可能重复投递（消费者要做幂等）

为什么会重复？
  消费者处理完 → 提交 offset 前宕机
  重启后 → 从上次 offset 重新消费
  → 同一条消息被处理两次
```

---

## 三、Producer 配置

### 3.1 依赖配置

```yaml
# application.yml
mafka:
  producer:
    app-key: ${mafka.app-key}  # 申请的 appKey
    bg-namespace: ccs_admin      # 业务命名空间
```

### 3.2 Bean 注入

```java
@Configuration
public class MafkaConfig {

    @Bean
    public MafkaClient mafkaClient(@Value("${mafka.app-key}") String appKey,
                                    @Value("${mafka.bg-namespace}") String bgNamespace) {
        return MafkaClientBuilder.builder()
                .appKey(appKey)
                .bgNamespace(bgNamespace)
                .build();
    }
}
```

---

## 四、发送消息

### 4.1 发送普通消息

```java
@Service
@Slf4j
public class TaskEventProducer {

    private static final String TOPIC_TASK_COMPLETED = "ccs_task_completed";

    @Autowired
    private MafkaClient mafkaClient;

    /**
     * 发送任务完成事件
     */
    public void sendTaskCompletedEvent(Long taskId, String staffMis) {
        // 1. 构建消息体（建议用 DTO，便于版本管理）
        TaskCompletedEvent event = TaskCompletedEvent.builder()
                .taskId(taskId)
                .staffMis(staffMis)
                .completedAt(LocalDateTime.now())
                .build();

        // 2. 序列化（JSON）
        String messageBody = JSON.toJSONString(event);

        // 3. 发送
        try {
            SendResult result = mafkaClient.sendMessage(TOPIC_TASK_COMPLETED, messageBody);
            if (!result.isSuccess()) {
                log.error("Send message failed: topic={}, taskId={}, reason={}",
                        TOPIC_TASK_COMPLETED, taskId, result.getErrorMessage());
                // 根据业务决定：重试？写 DB 补偿？告警？
            }
        } catch (MafkaException e) {
            log.error("Mafka exception when sending task completed event, taskId={}", taskId, e);
        }
    }
}
```

### 4.2 发送延迟消息

```java
/**
 * 发送延迟消息（N 秒后投递）
 * 场景：任务创建后 24 小时未完成，自动发送提醒
 */
public void sendTaskReminderDelayed(Long taskId, int delaySeconds) {
    TaskReminderEvent event = TaskReminderEvent.builder()
            .taskId(taskId)
            .build();

    try {
        // delaySeconds: 延迟秒数
        SendResult result = mafkaClient.sendDelayMessage(
                "ccs_task_reminder",
                JSON.toJSONString(event),
                delaySeconds  // 延迟 24*3600 = 86400 秒
        );
        log.info("Scheduled reminder for taskId={}, delay={}s", taskId, delaySeconds);
    } catch (MafkaException e) {
        log.error("Failed to send delay message for taskId={}", taskId, e);
    }
}

// 调用方式：
// sendTaskReminderDelayed(taskId, 24 * 60 * 60);  // 24小时后提醒
```

### 4.3 带 Key 的消息（有序消息）

```java
/**
 * 指定 messageKey 可以保证同一 key 的消息有序
 * 场景：同一员工的状态变更事件必须有序处理
 */
public void sendStaffStatusChange(String staffMis, StaffStatusChangeEvent event) {
    mafkaClient.sendMessage(
            "ccs_staff_status",
            JSON.toJSONString(event),
            staffMis  // messageKey = staffMis，同一员工的消息进入同一 Partition
    );
}
```

---

## 五、消息设计最佳实践

### 5.1 消息体设计原则

```java
// ✅ 好的消息体设计
@Data
@Builder
public class TaskCompletedEvent {
    private Long taskId;           // 业务 ID（消费者可查最新状态）
    private String staffMis;       // 关联实体
    private LocalDateTime eventAt; // 事件发生时间（非处理时间）
    private String eventId;        // 全局唯一ID（用于幂等）
    private Integer version;       // 消息版本号（兼容升级）
}

// ❌ 不好的消息体设计
@Data
public class BadEvent {
    private Map<String, Object> data;  // 无类型，难以版本管理
    // 缺少 eventId，无法幂等
    // 缺少 eventAt，时序不可追溯
}
```

### 5.2 Topic 命名规范

```
格式：{业务前缀}_{事件名称}_{可选环境}

✅ 好的命名：
  ccs_task_completed
  ccs_staff_onboard
  ccs_order_paid

❌ 坏的命名：
  test_topic          ← 无业务语义
  send_msg            ← 太泛
  task                ← 太短
```

### 5.3 发送失败处理策略

```
发送失败处理方案（按重要性选择）：

方案1：日志告警 + 人工处理（适合低频、非核心）
  → catch → log.error → 告警

方案2：本地重试（适合临时网络抖动）
  → catch → retry 3次 → 失败告警

方案3：写 DB 补偿表（适合强一致性要求）
  → 业务操作 + 写消息补偿表（同一事务）
  → 定时任务扫描补偿表重发
  → 发送成功后删除补偿记录
```

---

## 六、核心知识点总结

```
消息队列三大价值：
  异步 → 用户操作快速返回
  解耦 → 生产者不依赖消费者
  削峰 → 缓冲流量洪峰

关键概念：
  bgNameSpace → 业务隔离
  Topic       → 消息分类
  ConsumerGroup → 消费者组

Producer API：
  sendMessage(topic, body)           → 立即投递
  sendDelayMessage(topic, body, sec) → 延迟投递
  sendMessage(topic, body, key)      → 分区有序

投递语义：
  At Least Once → 消费者必须幂等！
```

---

## 课后练习

1. **基础：** 设计一个"员工离职"事件的消息体，包含哪些字段？为什么需要 `eventId`？
2. **进阶：** 如果一条重要消息发送失败，如何保证最终一定会被发送？设计一个重试方案
3. **思考：** 为什么有了消息队列还需要 RPC？什么情况下必须用 RPC 而不能用消息队列？

---

*← 上一课：Pigeon 高级特性 | 下一课：Mafka Consumer 设计 →*
