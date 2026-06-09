# Pigeon — 美团内部 RPC 框架

> 对标开源：Dubbo / gRPC
> 项目中位置：`ccsadmin-starter/src/main/java/.../pigeon/`

---

## 第一节：什么是 RPC，为什么需要 Pigeon

### 1.1 RPC 的本质问题

在单体应用里，调用一个方法就是一行代码：

```java
StaffDTO staff = staffService.queryByMis("zhangsan");
```

但在微服务架构下，`staffService` 可能部署在另一台机器上。**RPC（Remote Procedure Call，远程过程调用）** 让你像调用本地方法一样调用远端服务，隐藏掉网络通信的所有细节：

```
本地代码                         远端服务（MDM系统）
    │                                  │
    │── iStaffPigeonService.query() ──▶│  实际执行查询
    │                                  │
    │◀──────────── 返回结果 ───────────│
```

没有 RPC 框架，你需要自己：序列化请求 → 建立 TCP 连接 → 发送字节 → 等响应 → 反序列化 → 处理超时/重试。

### 1.2 Pigeon 在美团体系的位置

Pigeon 是美团自研 RPC 框架，集成在 MDP 中。相比开源 Dubbo 的优势：

| 特性 | Pigeon | Dubbo |
|------|--------|-------|
| 注册中心 | 美团内部 MNS | ZooKeeper/Nacos |
| 服务发现 | 自动（基于 AppKey）| 需配置 |
| 监控 | 对接美团 CAT/Sentry | 需额外接入 |
| 配置 | `@MdpPigeonClient` 一个注解 | 需配置 XML 或 YAML |

### 1.3 项目中 Pigeon 的整体用途

本项目（ccsadmin）对外暴露 6 个 Pigeon 服务，同时调用 10+ 个外部 Pigeon 服务：

```
外部系统调用本项目（Server端）：
  csc-cratos（主系统） ──Pigeon──▶ IncentiveServiceImpl（激励任务查询）
  前端/其他系统        ──Pigeon──▶ CcsOrgPigeonServiceImpl（组织架构）

本项目调用外部（Client端）：
  ccsadmin ──Pigeon──▶ MDM（员工信息）
  ccsadmin ──Pigeon──▶ 排班系统（班次信息）
  ccsadmin ──Pigeon──▶ 培训系统（课程内容）
```

---

## 第二节：Pigeon 核心架构

### 2.1 服务注册与发现

```
服务启动时：
  Provider（IncentiveServiceImpl）
      │── 向 MNS 注册 ──▶ [appKey: com.sankuai.csccratos.ccsadmin]
      │                   [service: IncentiveService]
      │                   [ip:port: 10.0.1.23:5001]

调用时：
  Consumer（其他系统）
      │── 向 MNS 查询 ──▶ "我要调 IncentiveService，给我地址"
      │◀── 返回地址列表 ── [10.0.1.23:5001, 10.0.1.24:5001]
      │── 负载均衡选一个，直接 TCP 连接调用
```

### 2.2 序列化协议

Pigeon 默认使用 **Hessian** 序列化，也支持 JSON。因此接口定义的参数/返回值必须是可序列化的 Java 对象（不能传 Stream、Connection 等）。

### 2.3 线程模型

```
调用方（Consumer）            服务方（Provider）
    │                              │
    │── 请求 ──▶ Pigeon Client     │
    │           │序列化            │
    │           │发送 TCP 包 ─────▶ Pigeon Server
    │           │                  │反序列化
    │           │                  │线程池执行业务方法
    │           │                  │序列化结果
    │◀──────────────── 响应 ───────│
```

Consumer 端默认**同步阻塞**等待响应，超时时间通过 `@MdpPigeonClient(timeout=3000)` 设置。

---

## 第三节：服务端实现（对外暴露接口）

### 3.1 最简示例

**第一步**：在 `ccsadmin-api` 模块定义接口（供调用方引用）：

```java
// ccsadmin-api/src/main/java/.../api/service/IncentiveService.java
public interface IncentiveService {
    ResultVO<Boolean> checkTrainingPacakge(Long packageId);
}
```

**第二步**：在 `ccsadmin-starter` 实现接口并加 `@MdpPigeonServer`：

```java
// ccsadmin-starter/.../pigeon/server/IncentiveServiceImpl.java
@MdpPigeonServer              // ← 这一个注解完成服务注册
@Slf4j
public class IncentiveServiceImpl implements IncentiveService {

    @Autowired
    private IncentiveMapperService incentiveMapperService;

    @Override
    public ResultVO<Boolean> checkTrainingPacakge(Long packageId) {
        try {
            List<IncentiveTask> tasks = incentiveMapperService
                .queryUnFinishTaskByContent(IncentiveTaskTypeEnum.CONTENT_TASK.getCode(), packageId);
            return ResultVO.newSuccessObject(CollectionUtils.isEmpty(tasks));
        } catch (Exception e) {
            log.error("checkTrainingPacakge error, packageId:{}", packageId, e);
            return ResultVO.newServerErrorObject();
        }
    }
}
```

### 3.2 @MdpPigeonServer 做了什么

1. 应用启动时，Pigeon 扫描带 `@MdpPigeonServer` 的 Bean
2. 自动向美团内部注册中心（MNS）注册服务
3. 在指定端口（默认 5001）监听来自其他系统的调用请求
4. 收到请求后，反序列化参数，调用对应方法，序列化结果返回

### 3.3 异常处理规范

对外的 Pigeon 接口必须**吞掉所有异常**，返回业务错误码，不能把异常抛到调用方：

```java
// ✅ 正确：try-catch 包裹，返回错误 ResultVO
public ResultVO<Boolean> checkPackage(Long packageId) {
    try {
        // 业务逻辑
        return ResultVO.newSuccessObject(result);
    } catch (BusiException e) {
        return ResultVO.newBusiErrorObject(e.getMessage());
    } catch (Exception e) {
        log.error("...", e);
        return ResultVO.newServerErrorObject();  // 返回通用错误
    }
}

// ❌ 错误：直接抛异常，调用方拿到 RPC 异常，无法区分业务错误
public Boolean checkPackage(Long packageId) throws Exception { ... }
```

---

## 第四节：客户端调用（调用外部服务）

### 4.1 字段注入方式（最常用）

```java
// StaffPigeonService.java
@Service
public class StaffPigeonService {

    // 注入外部服务的代理对象，timeout 单位毫秒
    @MdpPigeonClient(timeout = 10000)
    IStaffPigeonService iStaffPigeonService;      // 调用 MDM 的员工信息接口

    @MdpPigeonClient(timeout = 10000)
    IStaffRemoteService iStaffRemoteService;       // 调用 csc-cratos 的员工统计接口

    public Map<String, StaffRespDto> getStaffInfoByMisCode(Set<String> staffMisList) {
        StaffRequestDTO request = new StaffRequestDTO();
        request.setBizcode("cn.meituan.csc.supply.vcs.schedule");
        request.setAppkey("com.sankuai.csccratos.ccsadmin");

        // 调用远端方法，与本地方法无区别
        BizResoponseDTO<List<StaffRespDto>> result =
            iStaffPigeonService.batchGetStaffInfoByMisCodes(request);

        if (result.isSuccess()) {
            return buildMap(result.getData(), staffMisList);
        }
        return Collections.emptyMap();
    }
}
```

### 4.2 Configuration 类中注入（需要自定义参数时）

```java
// PigeonClientConfig.java
@Configuration
public class PigeonClientConfig {

    // 技能服务超时较短，单独配置 3000ms
    @MdpPigeonClient(timeout = 3000L)
    private PacificSkillRemoteService pacificSkillRemoteService;
}
```

适用场景：需要覆盖默认超时、或引入的接口没有被 Spring 组件扫描到时。

### 4.3 @MdpPigeonClient 工作原理

```
@MdpPigeonClient 是一个字段注解，由 MDP 的 BeanPostProcessor 处理：

1. 扫描到带 @MdpPigeonClient 的字段
2. 读取字段类型（如 IStaffPigeonService）
3. 向 MNS 查询该接口对应的服务地址
4. 创建一个动态代理对象注入到该字段
5. 调用时，代理对象负责：序列化 → 网络传输 → 反序列化 → 返回
```

### 4.4 批量调用与分页处理

调用外部 RPC 时，注意单次调用量限制：

```java
// 每次只传 20 条 MIS，防止对方接口超时
List<List<String>> partition = Lists.partition(new ArrayList<>(staffMisList), 20);
for (List<String> batch : partition) {
    request.setMisCodes(batch);
    BizResoponseDTO<List<StaffRespDto>> result =
        iStaffPigeonService.batchGetStaffInfoByMisCodes(request);
    if (result.isSuccess()) {
        allResults.addAll(result.getData());
    }
}
```

---

## 第五节：最佳实践与常见问题

### 5.1 超时设置原则

| 服务类型 | 建议超时 | 原因 |
|---------|---------|------|
| 简单查询（单条） | 1000-3000ms | 快速失败，不阻塞主流程 |
| 批量查询 | 5000-10000ms | 数据量大，耗时较长 |
| 写操作 | 5000ms | 需保证完成，但不能无限等 |

本项目中：`iStaffPigeonService` 设置了 10000ms（员工批量查询），`PacificSkillRemoteService` 设置了 3000ms。

### 5.2 防御性编程三要素

```java
public Optional<StaffDTO> getStaffByStaffMis(String staffMis) {
    // 1. 入参校验（避免无效 RPC 调用）
    if (staffMis == null) return Optional.empty();

    try {
        BaseResultDTO<StaffDTO> res = iStaffRemoteService.queryByLoginNameFromCache(staffMis);

        // 2. 业务结果校验（RPC 成功 ≠ 业务成功）
        return Optional.ofNullable(res.isSuccess() ? res.getData() : null);

    } catch (Exception e) {
        // 3. 异常降级（RPC 失败不影响主流程）
        log.error("Error querying staff by MIS: {}", staffMis, e);
        return Optional.empty();
    }
}
```

### 5.3 常见问题排查

**问题 1：调用方报 `com.dianping.pigeon.remoting.invoker.exception.ServiceUnavailableException`**
- 原因：服务端没启动，或注册中心里没有该服务
- 排查：检查服务端是否正常启动，`@MdpPigeonServer` 是否加了

**问题 2：调用报 `timeout`**
- 原因：超时时间太短，或服务端 GC/慢查询
- 排查：增大 `@MdpPigeonClient(timeout=xxx)`；检查服务端慢日志

**问题 3：序列化报错 `ClassNotFoundException`**
- 原因：调用方和服务方使用了不同版本的接口 API jar
- 排查：统一 `ccsadmin-api` 版本，检查 `pom.xml` 中 `api.version`

### 5.4 本项目 Pigeon 服务总览

```
对外暴露（Server）：
  IncentiveServiceImpl          → 激励任务查询（checkTrainingPacakge）
  CcsRankRewardPigeonServiceImpl → 排名奖励规则查询
  CcsOrgPigeonServiceImpl        → 组织架构数据查询
  RewardActivityRemoteService    → 奖励活动数据
  CcsTaskRewardServicePigeonImpl → 任务奖惩规则查询
  RecruitmentTaskPigeonServiceImpl → 招募任务信息

调用外部（Client）：
  StaffPigeonService    → MDM 员工信息、技能标签
  MdmCrowdPigeonService → MDM 人群查询
  StaffClassPigeonService → 排班系统
  TrainingPigeonService   → 培训系统
  HuaweiCloudWorkspacePigeonService → 华为云桌面
```
