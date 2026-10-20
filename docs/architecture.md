# AstraRelay：完整源码、共享上下文与服务边界

本文补充 [README](../README.md)。本仓库是完整脱敏开发源码，不是上下文摘录；业务、数据库、网页、桌面和测试均保留。私有接入标识已通用化，运行配置/数据及内部维护记录不发布。下文区分当前代码、宿主责任与未来方向，不将接口存在等同于业务集成完成。

## 完整源码布局

| 层 | 实际模块 | 边界 |
| --- | --- | --- |
| Web 宿主 | [app.py](../app.py)、[auth.py](../auth.py)、[admin_authorization.py](../admin_authorization.py)、`templates/`、`static/` | 路由、会话、CSRF、归属校验、公开响应 |
| 协作执行 | [broadcast.py](../broadcast.py)、[workspace_stream_service.py](../workspace_stream_service.py) | 旧提及解析、选中模型、上下文、流式事件与持久化 |
| 上下文 | [collaboration_context.py](../collaboration_context.py)、[memory_manager.py](../memory_manager.py) | 共享选择器与历史摘要基础，不是自治 Memory |
| 接入与目录 | [ai_adapter.py](../ai_adapter.py)、[model_registry.py](../model_registry.py)、[model_catalog.py](../model_catalog.py)、[catalog_refresh.py](../catalog_refresh.py) | 适配、可调用模型解析、缓存、刷新/降级 |
| 公共投影 | [public_model_directory.py](../public_model_directory.py)、[model_capabilities.py](../model_capabilities.py) | 公共名称、能力分类，信息收录与调用资格分离 |
| 文件与媒体 | [attachment_service.py](../attachment_service.py)、[media_service.py](../media_service.py) | 附件提取/归属与模型模态适配 |
| 数据与账户 | [database.py](../database.py)、[user_database.py](../user_database.py)、[email_auth.py](../email_auth.py)、[sms_auth.py](../sms_auth.py) | 共享账本/兼容存储、账户、邮件恢复、可选短信 |
| 开发期商业基础 | [billing_gateway.py](../billing_gateway.py)、[payment_service.py](../payment_service.py)、[payments/providers.py](../payments/providers.py) | 预冻结、结算/回滚、支付适配与本地模拟，非公众支付 |
| 独立 Memory | [memory/models.py](../memory/models.py)、[memory/access.py](../memory/access.py)、[memory/repository.py](../memory/repository.py)、[memory/mysql_repository.py](../memory/mysql_repository.py)、[memory/migrations.py](../memory/migrations.py)、[memory/vector.py](../memory/vector.py) | 类型、范围、仓储、迁移和向量边界，尚无 Director/Agent/聊天业务闭环 |
| 桌面边界 | [desktop/src/main.cjs](../desktop/src/main.cjs)、[desktop/src/preload.cjs](../desktop/src/preload.cjs)、[desktop/src/cloud-session.cjs](../desktop/src/cloud-session.cjs)、[desktop/src/project-service.cjs](../desktop/src/project-service.cjs) | 主进程受控云端/项目能力与渲染隔离 |

`memory/` 是产品源代码，`data/` 是排除的运行数据。旧表和兼容模型存储不意味着恢复独立人格；工作台方向仍是同一共享账本。四个历史源文件 `ai_adapter_old.py`、`browser_controller.py`、`database_old.py`、`main_old.py` 也保留，但当前运行时不导入；旧适配器有原有语法缺陷，不能当作有效入口或功能保证。

## 当前请求链路

```text
用户 / 桌面主进程
  → Flask 身份、CSRF、会话归属、输入与模型能力检查
  → 会话账本与当前请求状态
  → CollaborationContextService：选择共同历史
  → AIAdapter：服务端模型请求（可带开发计费网关）
  → 回复保存 / 同步确认 / SSE 安全呈现
  → 用户审阅与下一步选择
```

这是职责顺序概览，不承诺所有入口共享同一跨系统事务。`create_app` 默认创建真实 `Database` / `UserDatabase`、初始化库表、建立适配与业务服务，并启动目录刷新和健康检查线程；测试可注入依赖，`public_demo.py` 是附加隔离预览，正常路径未替换为 mock。

普通登录、找回与注册依赖账户/邮件流程；管理员能力单独校验。新增模型通过可调用目录与能力检查，再使用当前共享快照。SSE 中断与任务失败不是同一状态；请求成功、落库、游标和结算应分别检查，UI 完成动画不是事务证据。

## 数据与归属契约

共享上下文工具依赖两个注入接口：

```python
get_shared_messages(conversation_id, limit=None) -> list[dict]
mark_model_context_synced(conversation_id, model_id, message_id) -> None
```

数据库应返回按事件顺序排列且只属于已授权会话的记录。上下文工具不排序，没有当前用户参数，不负责登录、CSRF 或所有权；不能将客户端会话 ID 当作授权。`custom` 只在返回历史内筛选，仍依赖宿主先完成归属校验。未来租户隔离须同时落实读取、写入与索引边界，多用户不等于多租户。

| 字段 | 当前用途 | 前置条件/风险 |
| --- | --- | --- |
| `id` | 选择集合和同步位置 | 稳定、唯一、可哈希；缺失/重复可能扩大选择 |
| `role` | 区分用户说话者 | 当前只专门区分 `user` |
| `content` | 拼接文本 | 宿主校验类型、长度与敏感范围 |
| `model` | 模型说话者标签 | 已知别名用公共名，未知原值回退不等于安全公开投影 |
| `message_type` | `summary` 为已有摘要 | 不检验摘要真实性、来源、时效 |

输出为空列表，或 `[{"role": "system", "content": "当前协作上下文：…"}]`，不携带 token、成本、版本或授权信息。宿主应避免把同一用户消息重复加入历史和当前请求。

## 当前上下文算法与限制

默认 `history_limit=24`。`smart` 收集所有 `summary`，再取最后 N 条非摘要消息，合并 ID 后按原顺序输出；`full` 不筛选；`custom` 按 ID 选择；`from_now` 直接返回 `[]`，不查询历史，不保存时间游标。未知 mode 抛出 `ValueError`，空历史/无匹配不伪造摘要。

目标 `model_id` 不参与历史筛选，新加入者能看到其他参与模型的记录。`mark_synced` 只是委托数据库，不按游标增量构造，不证明理解，不提供幂等或自动重试。

算法读取全量历史，选择处理为 O(n)，内存和格式化成本还取决于内容长度。它不执行 tokenizer，不生成摘要，不提供严格 token 上限；全部摘要会持续增长，单条也可很长。构造器不校验 `history_limit`：0 的 `[-0:]` 实际保留全部，负数按 Python 切片处理，不能当作安全限额。后续需补边界测试，再引入摘要生命周期、窗口查询与信息丢失提示。

历史拼为 system 消息便于兼容，也可能提升不可信内容的指令地位。说话者标签不是可信级别。应评估角色结构、引用边界、来源/版本和提示注入测试；多模型一致不等于独立证据。摘要不是脱敏器，token 截断不是隐私控制。

## Adapter Gateway 兼容契约

```python
AIAdapter.reply_with_history(message, history, model="deepseek")
# 返回纯文本 str
```

这是实际代码接口，不只是未来设计。默认值兼容旧调用方，交互新会话仍无预选模型。`BroadcastService` 提及解析与 `selected_models` 生成保持旧契约。

传入历史须经宿主批准，模型须可调用且能力匹配。服务端环境保存连接和凭据，公共元数据只投影公共名。流式事件、使用量、结构化错误是另一契约，不能悄悄将旧字符串改为对象或迭代器。通用渠道配置不意味着自动成本最优路由已经实现。

## 配置变量与启动行为

[config.py](../config.py) 读取进程环境，不自动加载 `.env`。全空 [.env.example](../.env.example) 是名称清单，不能整体导入。未设置与设为空不同：整数空值解析失败，布尔空值为 false，字符串空值可能覆盖默认值。真实值仅在私有启动环境提供，不提交或打印。`DATABASE_URL` **未来专用、当前未实现**；MySQL 只使用显式变量。

| 名称 | 支持范围与默认行为 |
| --- | --- |
| `MODEL_GATEWAY_BASE_URL`, `MODEL_GATEWAY_API_KEY` | 主渠道地址/凭据，默认空；无实际内置端点 |
| `GATEWAY_COMPAT_BASE_URL`, `GATEWAY_COMPAT_API_KEY`, `GATEWAY_COMPAT_API_KEYS` | 兼容渠道与逗号分隔 key pool；未设置 pool 时回退单 key |
| `GATEWAY_SECONDARY_BASE_URL`, `GATEWAY_SECONDARY_API_KEY`, `GATEWAY_SECONDARY_API_KEYS`, `GATEWAY_SECONDARY_CATALOG_URL` | 次渠道连接、独立 pool 与目录 URL，默认空 |
| `DEFAULT_MODEL_RUNTIME`, `USE_LEGACY_API` | 主 key 存在时运行时默认 `gateway_main`，否则 `legacy`；后者默认 false，不表示打开即可调用 |
| `MODEL_CATALOG_CACHE_PATH` | 默认 `data/model_catalog.json`，缓存不附带，启动可刷新 |
| `MYSQL_HOST`, `MYSQL_PORT`, `MYSQL_USER`, `MYSQL_PASSWORD` | 默认 localhost/3306/root/空；隔离开发应使用专用用户，不能依赖 root 空口令 |
| `MYSQL_DATABASE`, `USER_DATABASE` | 默认 `chat_db` / `user_db`；普通服务初始化聊天/账户库，保留固定分组/模型库与兼容迁移 |
| `MEMORY_DATABASE` | 默认 `memory_db`，独立迁移/仓储配置；普通 `create_app` 不初始化它 |
| `FLASK_SECRET_KEY` | 无可用默认值，至少 32 字符；认证初始化拒绝不合格配置 |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD`, `SMTP_FROM_EMAIL`, `SMTP_USE_TLS` | 注册/恢复；端口默认 587、TLS 默认 true，发件人默认回退用户名；未完整配置则不可用 |
| `EMAIL_CODE_PEPPER` | 独立至少 32 字符高熵值，拒绝明显占位或低复杂度重复值 |
| `SMS_AUTH_ENABLED`, `ALIBABA_CLOUD_ACCESS_KEY_ID`, `ALIBABA_CLOUD_ACCESS_KEY_SECRET`, `ALIYUN_SMS_SIGN_NAME`, `ALIYUN_SMS_BIND_TEMPLATE_CODE` | 可选短信，开关默认 false；须完整授权配置，模板默认常量不等于可用服务 |
| `ADMIN_USERNAME`, `ADMIN_PASSWORD` | 可选管理员引导，默认空；会创建/提升管理员，普通试运行不设置；Windows bootstrap 可能持久化会话密钥 |
| `ENABLE_MOCK_PAYMENTS`, `MOCK_PAYMENT_CALLBACK_TOKEN` | 本地模拟开关/回调令牌，默认禁用；非真实支付开关 |
| `MODEL_PRICES_MICRO_POINTS`, `EXCHANGE_RATE` | 前者为逗号分隔 `model=正整数` 开发价格，空表不启用正常适配器的计费网关；后者默认 100，不是运营许可 |
| `TOKEN_CHARS_PER_TOKEN`, `MAX_OUTPUT_TOKENS`, `MAX_IMAGE_UPLOAD_BYTES` | 默认 4、1024、5 MiB；字符估算不是实际 tokenizer/计费 |
| `HOST`, `FLASK_DEBUG` | 默认回环与 debug=false；端口代码固定 5000，无 `PORT` 环境覆盖 |
| `DATABASE_URL` | 仅未来发布侧契约，不被当前连接代码读取，不能代替 `MYSQL_*` |

`GROUP_CHAT_DATABASE` 和各历史模型库名是代码常量，不支持同名环境覆盖。正常启动要求专用 MySQL 用户在独立开发实例上具备建库、表迁移及读写权限。启动会写数据/迁移，配置网关后可能发起后台请求，不能指向业务库。SMTP 未配置时页面可呈现，但注册/找回不可用，预览不会绕过。

独立 Memory 通过 `MemoryDatabase.initialize()` 显式迁移，使用版本表和数据库锁；它是底层能力，不是自动聊天集成。真实 MySQL 验证由 [tests/run_memory_mysql_integration.ps1](../tests/run_memory_mysql_integration.ps1) 管理隔离容器与 opt-in，默认 Python 测试不运行真实实例。

## 桌面信任边界

主进程管理 allowlist 云端操作、cookies、CSRF 与事件投影；渲染层 sandbox/contextIsolation 开启、Node 关闭，不拿服务端凭据。`ASTRARELAY_BASE_URL` 是桌面进程变量；打包态显式配置的云端地址要求 HTTPS，未配置仍回退本地回环地址，不代表正式分发配置；开发允许回环 HTTP。项目模块只操作用户选定根目录，限制路径、受保护文件、类型/大小；编辑保存不是任意文件访问许可。

Terminal、Patch、Git、Skills、Full Access 与 Offline 状态不代表已启用执行。安排视图也不是后端持久化自治调度系统。自动测试与历史本地打包不替代登录、会话过期、项目、签名更新等人工验收。依赖风险见 [SECURITY.md](../SECURITY.md)。

## 失败模式、验证与演进

| 情况 | 当前边界 / 后续责任 |
| --- | --- |
| 账本读取/同步写入异常 | 工具直接传播，宿主安全分类，不吞错为成功 |
| 模型不可用、超时、能力不符 | 解释限制并安全失败，不能以目录存在判定成功 |
| SSE 断线/重试 | 区分传输与任务状态，避免重复保存/调用/计费，不假设跨系统恰好一次 |
| 请求期间新消息 | 工具无快照版本/锁，应明确事件上界与一致性 |
| 摘要增长、重复/缺失 ID、超长文本 | 当前不保证严格预算，需增强边界与质量评估 |
| 私有文件、其他用户/租户数据 | 数据出入口必须授权/过滤/脱敏，不能交给模型自觉 |

完整 Python、网页 JS、桌面测试随仓库提供，[README](../README.md) 列出命令。多使用合成数据库/客户端，验证局部行为而非真实云端可用性；上下文三项局部测试并非整个项目测试范围。

未来 AI Team OS 应分离职责：Director 提出计划与分工；Agent 在批准工具范围内执行；Memory 治理来源、版本、范围、保留/删除与质量；Model Router 匹配能力、成本和可用性。用户与宿主负责身份、预算、批准、取消与审计。未来业务集成尚未完成，现有 Memory/向量接口没有赋予模型高权限。
