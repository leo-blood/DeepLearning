# Mafka — 美团内部消息队列

> 对标开源：Kafka
> 项目中配置：`ccsadmin-starter/src/main/profiles/prod/mafka.properties`
> 项目中代码：`ccsadmin-starter/src/main/java/.../mq/`

---

## 第一节：什么是消息队列，为什么需要 Mafka

### 1.1 消息队列解决的核心问题

**场景**：激励任务创建后，需要在任务开始时间（可能是明天）自动圈人并推送。

如果同步处理：

```
HTTP 请求创建任务
    │
    │── 立即执行圈人（耗时10分钟）── 超时！用户等不了
    │── 等到开始时间才创建...   ── 服务器内存要保存这个"等待"
```

引入消息队列后：

```
HTTP 请求创建任务
    │── 存库 + 投递延迟消息 ──▶ Mafka（持久化）── 立即返回给用户
                                     │
                              （到开始时间时）
                                     │
                             Consumer 收到消息 ──▶ 执行圈人/推送
```

消息队列的三大作用：
- **异步**：不阻塞主流程，提升响应速度
- **解耦**：生产者不需要知道消费者是谁
- **削峰**：大量事件到来时，消费者按自己节奏处理

### 1.2 Mafka 核心概念

```
Topic（主题）
  └── 消息的逻辑分类，如 "ccsadmin_delay_issue_tasks"

Producer（生产者）
  └── 向 Topic 发送消息的一方

Consumer（消费者）
  └── 从 Topic 读取消息的一方

ConsumerGroup（消费者组）
  └── 同一 Group 内多台机器只有一台消费同一条消息
  └── 不同 Group 都能收到同一条消息（广播）

bgNameSpace（业务命名空间）
  └── 隔离不同业务线的 Topic，如 "pingtai"、"xm"（大象）
```

### 1.3 Mafka 与 Kafka 的关系

Mafka 是基于 Kafka 封装的，对开发者屏蔽了：
- Broker 地址管理（通过 bgNameSpace 自动发现）
- 消费者 offset 管理（框架自动提交）
- 序列化（直接传 String，框架不做干预）

---

## 第二节：生产者配置详解

### 2.1 mafka.properties 配置结构

```properties
# 生产者配置，支持多个（索引 0,1,2...）
mdp.mafka.producer[0].producerName=incentivesNotifyProducer    # Bean 名称
mdp.mafka.producer[0].bgNameSpace=pingtai                       # 业务命名空间
mdp.mafka.producer[0].appkey=com.sankuai.csccratos.ccsadmin    # 应用标识
mdp.mafka.producer[0].topicName=incentive_task_notify_producer  # Topic 名
mdp.mafka.producer[0].delay=true                                # 是否支持延迟消息

mdp.mafka.producer[1].producerName=delayAssignIncentiveTaskProducer
mdp.mafka.producer[1].bgNameSpace=pingtai
mdp.mafka.producer[1].appkey=com.sankuai.csccratos.ccsadmin
mdp.mafka.producer[1].topicName=ccsadmin_delay_issue_tasks
mdp.mafka.producer[1].delay=true                                # ← 延迟消息
```

配置完成后，MDP 自动创建名为 `delayAssignIncentiveTaskProducer` 的 `IProducerProcessor` Bean，供注入使用。

### 2.2 Producer 代码实现

```java
// DelayDoIncentiveTaskProducer.java
@Service
@Slf4j
public class DelayDoIncentiveTaskProducer {

    // ① 通过 @Qualifier 注入对应配置的 Producer Bean
    @Autowired
    @Qualifier("delayAssignIncentiveTaskProducer")
    private IProducerProcessor producer;

    public void distributeIncentiveTaskByDelayMQ(IncentiveTask incentiveTask) {
        // ② 构建消息体（通常是 JSON 字符串）
        IncentiveDistributeTaskInfoBO bo = new IncentiveDistributeTaskInfoBO();
        bo.setTaskId(incentiveTask.getId());
        bo.setTaskVersion(incentiveTask.getVersion());
        String jsonString = JSONUtil.toJSONString(bo);

        // ③ 计算延迟时间（距任务开始时间的毫秒数）
        long remainingTime = incentiveTask.getTaskStartDate().getTime() - System.currentTimeMillis();
        long delayTime = remainingTime > 5000 ? remainingTime : 5000;  // 最少延迟5秒

        try {
            // ④ 发送延迟消息
            ProducerResult result = producer.sendDelayMessage(jsonString, delayTime);
            log.info("发送延迟消息成功, taskId:{}, delay:{}ms", bo.getTaskId(), delayTime);
        } catch (Exception e) {
            log.error("发送延迟消息失败", e);
        }
    }
}
```

### 2.3 两种发送方式对比

```java
// 普通消息（立即投递）
producer.sendMessage(jsonString);

// 延迟消息（指定毫秒后才能被消费）
producer.sendDelayMessage(jsonString, delayMillis);
// 注意：delay=true 的 Producer 才能用 sendDelayMessage
```

---

## 第三节：消费者配置详解

### 3.1 mafka.properties 消费者配置

```properties
# 消费者配置
mdp.mafka.consumer[0].bgNameSpace=pingtai
mdp.mafka.consumer[0].appkey=com.sankuai.csccratos.ccsadmin
mdp.mafka.consumer[0].topicName=cloud.customer.service.improve  # 订阅的 Topic
mdp.mafka.consumer[0].subscribeGroup=training.progress.notify.consumer.group  # 消费者组名
mdp.mafka.consumer[0].listenerId=trainingProgressNotifyConsumer  # 对应 Bean 名
```

**关键字段说明**：
- `subscribeGroup`：同一组内多台机器竞争消费，每条消息只被处理一次
- `listenerId`：要绑定的消费者 Bean 的 Spring Bean 名称（`@Service("xxx")` 的值）

### 3.2 Consumer 代码实现

```java
// TrainingProgressNotifyConsumer.java
@Service                            // ← Bean 名默认是类名首字母小写
@Slf4j
public class TrainingProgressNotifyConsumer {

    @Autowired
    private IncentiveMapperService incentiveMapperService;

    /**
     * 消费方法：参数固定为 (String msgBody, MdpMqContext ctx)
     */
    @MdpMafkaMsgReceive             // ← 标记这是消息消费方法
    public ConsumeStatus exec(String msgBody, AbstractMdpListener.MdpMqContext ctx) {
        try {
            log.info("收到消息: {}", msgBody);
            // 反序列化消息体
            TrainingProgressBO bo = JSON.parseObject(msgBody, TrainingProgressBO.class);
            // 业务处理
            consume(bo);
            // 返回成功，框架提交 offset
            return ConsumeStatus.CONSUME_SUCCESS;

        } catch (BusiException e) {
            // 业务异常：不需要重试，直接消费成功（跳过该消息）
            log.info("业务异常，跳过消息: {}", msgBody, e);
            return ConsumeStatus.CONSUME_SUCCESS;

        } catch (Exception e) {
            // 系统异常：返回失败，框架会重试
            log.error("消费失败，等待重试: {}", msgBody, e);
            return ConsumeStatus.CONSUME_FAILURE;
        }
    }
}
```

### 3.3 另一种 Consumer 写法（指定 Bean 名）

```java
// TrainingResultNotifyConsumer.java
@Service("trainingResultNotifyConsumer")  // ← 显式指定 Bean 名，与 listenerId 对应
@Slf4j
public class TrainingResultNotifyConsumer {

    @MdpMafkaMsgReceive
    protected ConsumeStatus receive(String msgBody, AbstractMdpListener.MdpMqContext ctx) {
        // ...
    }
}
```

当类名和 Bean 名不一致时，必须用 `@Service("xxx")` 显式指定。

---

## 第四节：延迟消息与自产自消

### 4.1 延迟消息原理

```
任务创建（T=0）
    │
    │── sendDelayMessage(msg, 86400000) ──▶ Mafka 存储
    │                                           │
    │                                    （24小时后）
    │                                           │
    │                                    Consumer 收到消息
    │                                           │
    │                                    执行圈人/推送
```

延迟消息的核心价值：**不需要定时轮询数据库**，到点自动触发，节省数据库压力。

### 4.2 自产自消模式（高风险）

本项目有两个 Topic 采用自产自消，即同一个应用既是 Producer 又是 Consumer：

```
ccsadmin_delay_issue_tasks:
  Producer[1] → sendDelayMessage → Mafka
                                    ↓（延迟到达）
                          Consumer[2] 消费 → 执行圈人逻辑
```

**为什么这样设计（ADR-002）**：
利用 Mafka 的延迟特性实现精确到毫秒的延迟触发，不需要额外的延迟任务系统。

**高风险点**：

```java
// ❌ 危险：消费逻辑里又发送相同消息 → 死循环
@MdpMafkaMsgReceive
public ConsumeStatus consume(String msgBody) {
    // 处理业务...
    producer.sendMessage(msgBody);  // 再次发同一 topic → 无限循环！
    return ConsumeStatus.CONSUME_SUCCESS;
}

// ✅ 正确：消费逻辑里用版本号或状态校验防止重复
@MdpMafkaMsgReceive
public ConsumeStatus consume(String msgBody) {
    IncentiveDistributeTaskInfoBO bo = JSON.parseObject(msgBody, ...);
    IncentiveTask task = incentiveMapperService.queryById(bo.getTaskId());
    // 版本号不匹配说明任务已被修改，跳过
    if (!bo.getTaskVersion().equals(task.getVersion())) {
        return ConsumeStatus.CONSUME_SUCCESS;
    }
    // 正常处理...
}
```

---

## 第五节：幂等性设计与最佳实践

### 5.1 为什么消费必须幂等

消息队列的投递保证是 **at-least-once（至少一次）**，网络抖动可能导致同一条消息被消费多次：

```
场景：Consumer 处理完消息，提交 offset 前挂了
  → Mafka 认为消息未消费，重新投递
  → 同一条消息被处理两次！
```

### 5.2 幂等实现方案

**方案 1：数据库唯一键**

```java
// 插入前先查，已存在则跳过
int count = mapper.countByTaskIdAndStaffMis(taskId, staffMis);
if (count > 0) {
    return ConsumeStatus.CONSUME_SUCCESS;  // 已处理，跳过
}
mapper.insert(record);
```

**方案 2：版本号校验（本项目使用）**

```java
IncentiveTask task = incentiveMapperService.queryById(bo.getTaskId());
if (!bo.getTaskVersion().equals(task.getVersion())) {
    log.info("版本不匹配，跳过 taskId:{}", bo.getTaskId());
    return ConsumeStatus.CONSUME_SUCCESS;
}
```

**方案 3：状态机校验**

```java
// 只处理特定状态的任务，状态变更后重复消费不会再处理
if (task.getStatus() != TaskStatusEnum.TO_BE_ISSUED.getCode()) {
    return ConsumeStatus.CONSUME_SUCCESS;
}
```

### 5.3 消息格式设计规范

消息体应该包含足够的信息让 Consumer 独立工作：

```json
// ✅ 好的消息格式：包含必要上下文
{
  "taskId": 12345,
  "taskVersion": 3,          // 用于幂等校验
  "triggerTime": 1710000000  // 用于时序校验
}

// ❌ 差的消息格式：只有 ID，Consumer 要多次查库
{
  "taskId": 12345
}
```

### 5.4 本项目 Mafka 全景

```
生产者（6个）：
  incentivesNotifyProducer         → 激励任务通知（自产自消）
  delayAssignIncentiveTaskProducer → 延迟圈人（自产自消，delay=true）
  ccsOrgProcessProducer            → 组织架构处理
  sendWechatMessageProducer        → 发微信消息
  triggerMessageDeliveryTaskProducer → 触发消息投递
  smartButlerControlResultProducer → 智能管家控制结果（bgNameSpace 不同！）

消费者（12个）：
  trainingProgressNotifyConsumer   → 培训进度通知
  incentiveTaskNotifyConsumer      → 激励任务通知（接收自产）
  issueScheduledIncentiveTaskConsumer → 延迟圈人（接收自产）
  staffDtsConsumer                 → 员工 DTS 同步
  ccsOrgProcessConsumer            → 组织架构处理
  skillDtsConsumer                 → 技能 DTS 同步
  trainingResultNotifyConsumer     → 云办公培训结果
  miniprogramTrainingImportedResultConsumer → 小程序培训结果
  staffLifeCycleHandlerConsumer    → 员工入离职（bgNameSpace=xm！）
  triggerMessageDeliveryTaskConsumer → 消息投递触发
  miniprogramTrainingGroupCreateConsumer → 小程序培训群创建
  smartButlerMessageConsumer       → 智能管家工单质检
```

**注意**：`staffLifeCycleHandlerConsumer` 的 `bgNameSpace=xm`（大象体系），与其他 Topic 的 `pingtai` 不同，是常见踩坑点。
