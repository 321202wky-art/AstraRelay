# AstraRelay · 多模型协作工作台

把多个模型放进同一个任务空间和统一工作台，让它们接续工作，而不是让人反复搬运上下文。

AstraRelay 是我的个人全职探索项目，从多模型群聊与记忆原型演进为 Flask 网页和 Windows Electron 桌面协作工作台。本仓库现在提供**完整脱敏开发源码**，不是五文件摘录：账户、模型接入、附件、积分账本、支付适配、数据库、桌面端与产品测试均保留，供技术交流、面试走读和隔离开发验证。

“完整”指开发源码范围，不表示附带真实运行环境或成熟商业服务。发布差异包括私有传输名称、地址与配置名的通用化；不包含凭据、真实用户数据、本地配置、内部记录和原始开发历史；测试使用合成数据；另有可选离线预览和经过回归验证的 CSRF 加固。它不是与私有开发逐字节相同的镜像，后续更新仍需人工脱敏、测试和审阅。

建议阅读：[问题与个人工作](#项目问题与个人工作) → [源码导航](#完整源码导航) → [上下文算法](#代码走读共享上下文) → [完整服务设置](#完整服务设置) → [架构细节](docs/architecture.md)。已知依赖风险见 [SECURITY.md](SECURITY.md)；本次是源码发布，不是安装包或生产部署发布。

## 项目问题与个人工作

多模型使用中的困难不只是再接一个 API，而是协作状态丢失：需求在一个窗口、修改理由在另一个窗口，新加入的模型不知道已有约束。用户既要比较输出，又要搬运上下文、整理结论。

项目因此把会话记录进一步视为协作账本：用户需求、模型回复与已有阶段摘要进入同一条记录，每次请求再从中选择上下文。模型可以分工，但不各自维护互不相通的人格记忆。用户决定谁参与、共享什么，以及下一步是否执行。

作为个人项目，我持续负责产品定义、网页与桌面交互、Flask 路由和存储、模型适配、共享上下文、SSE、账户与账本边界，以及回归测试和发布整理。开发中使用 AI 辅助实现、检查与迭代；方案取舍、权限范围和验收判断仍由我负责。希望展示的是如何从真实问题落到状态管理、接口契约和可验证代码，而不只是一次模型成功回答。

## 当前实现与未完成边界

| 领域 | 可检查的开发实现 | 不应推断为已经完成 |
| --- | --- | --- |
| 网页协作 | 会话增删改、公共模型选择、共享历史、上下文模式、附件、SSE、模型广场 | 自治规划、任意工具执行、生产级高并发保证 |
| 模型接入 | 服务端适配、动态目录、能力分类、安全公共名称、可调用性校验 | 所有收录模型在任意部署均可用；全自动最优路由 |
| 账户与业务基础 | 邮箱注册/找回、可选短信绑定、管理员能力、事务积分账本与模拟支付 | 企业多租户、真实公众支付、经营合规或服务等级承诺 |
| 桌面 | 沙箱外壳、云端会话、显式授权项目根目录、受限搜索与文本编辑、本地安排视图 | 终端、Patch、Git、Skills、Full Access 或离线同步已开放 |
| Memory | 类型、访问范围、仓储接口、MySQL 迁移、向量边界与测试 | 长期记忆已经连接全部聊天请求或自治编排 |

当前尚未完成商业化，未开放真实公众充值、收费或提现。低层 Memory 模块已存在，但未实现 Director / Agent / Memory 的业务集成；AI Team OS 是路线方向，不是交付声明。

## 完整源码导航

| 阅读入口 | 关注的工程问题 |
| --- | --- |
| [app.py](app.py)、[auth.py](auth.py)、[admin_authorization.py](admin_authorization.py) | 应用工厂、路由、会话归属、登录与 CSRF、管理员能力 |
| [broadcast.py](broadcast.py)、[workspace_stream_service.py](workspace_stream_service.py) | 兼容广播入口、任务状态、工作台流式请求与结果保存 |
| [collaboration_context.py](collaboration_context.py)、[memory_manager.py](memory_manager.py) | 共享上下文选择与历史摘要基础，不是自动长期记忆平台 |
| [ai_adapter.py](ai_adapter.py)、[model_registry.py](model_registry.py)、[model_catalog.py](model_catalog.py) | Adapter Gateway、模型解析、目录缓存与安全降级 |
| [model_capabilities.py](model_capabilities.py)、[public_model_directory.py](public_model_directory.py) | 可调用/信息型目录区分、能力提示与公共元数据 |
| [attachment_service.py](attachment_service.py)、[media_service.py](media_service.py) | 文件提取、附件归属、模态适配与媒体调用 |
| [database.py](database.py)、[user_database.py](user_database.py) | 会话账本、兼容存储与迁移、账户、积分事务 |
| [billing_gateway.py](billing_gateway.py)、[payment_service.py](payment_service.py)、[payments/providers.py](payments/providers.py) | 预冻结/结算/回滚、支付接口及本地模拟适配器 |
| [memory/access.py](memory/access.py)、[memory/mysql_repository.py](memory/mysql_repository.py)、[memory/migrations.py](memory/migrations.py) | 独立 Memory 的授权范围、仓储和版本迁移 |
| [desktop/src/main.cjs](desktop/src/main.cjs)、[desktop/src/cloud-session.cjs](desktop/src/cloud-session.cjs)、[desktop/src/project-service.cjs](desktop/src/project-service.cjs) | 主进程信任边界、云端会话、受控本地项目读写 |
| [templates/](templates/)、[static/workspace.js](static/workspace.js) | Jinja2 页面、原生 JavaScript 和 SSE 交互 |
| [tests/test_app.py](tests/test_app.py)、[tests/test_collaboration_context.py](tests/test_collaboration_context.py)、[desktop/tests/](desktop/tests/) | API/边界回归、共享上下文合成测试、桌面契约测试 |

还保留 `ai_adapter_old.py`、`browser_controller.py`、`database_old.py`、`main_old.py` 四个历史文件。当前应用不导入它们；旧适配器存在历史语法错误，不是可运行入口，不在此次发布中顺带重写。实际入口始终是 `app.py`。更多契约、配置和失败模式见 [docs/architecture.md](docs/architecture.md)。

## 架构与人类控制

```text
用户目标、模型显式选择与批准
    → 网页 / 桌面工作台
    → Flask：身份、归属、请求与能力校验
    → 共享会话账本 → 上下文选择 → 服务端 Adapter Gateway
    → 参与模型 → 回复持久化与 SSE 呈现 → 用户审阅
```

orchestrator-worker（编排者—工作者）是演进方向：主脑提出任务拆解、分工和汇总建议，工作者完成有界步骤，不把某个模型永久绑定为某个角色。现有协作链路不等于已实现自治 Director、通用 Agent 调度、失败补偿或自我验证。

用户显式选择参与模型，新会话不预选模型；可随时增删模型、调整上下文模式。中途加入者使用当前共享快照接续工作。模型建议不等于操作授权；本地项目授权也不能扩大为终端、Git 推送或发布授权。

Adapter Gateway 是服务端架构边界，不是另起一个已部署网关服务。保留 `AIAdapter.reply_with_history(message, history, model="deepseek")` 的签名和纯文本返回；历史默认值不改变交互层无预选规则。设计目标是不硬编码供应商或模型，现实中仍保留兼容别名和配置，尚未消除历史耦合。公开 UI 只展示公共模型名称，不下发传输标识或凭据。

模型广场 `/model-plaza` 是公开信息目录，工作台只接受当前部署可调用的聊天模型。目录收录不代表调用资格；能力不匹配应提示限制并建议兼容辅助模型，不伪造成功。SSE 只是输出通道，不是授权、落库或结算完成的证明。

## 代码走读：共享上下文

[CollaborationContextService](collaboration_context.py) 依赖注入数据库对象，没有自己的网络客户端。`build(conversation_id, model_id, mode="smart", message_ids=None)` 从 `get_shared_messages(..., limit=None)` 读取有序共享记录，返回空列表或一条带说话者标签的 system 消息。

| 模式 | 实际行为 | 限制 |
| --- | --- | --- |
| `smart` 默认 | 保留所有已有摘要和最后 `history_limit=24` 条非摘要记录，维持原顺序 | 限制条数，不限制单条长度或摘要总数 |
| `full` | 使用全部共享历史 | 随会话增长而增大 |
| `from_now` | 不读取历史，直接返回 `[]` | 不建立持久时间游标 |
| `custom` | 只选当前历史中与 `message_ids` 匹配的记录 | ID 不是授权，宿主须先校验会话归属 |

`model_id` 不用于筛掉其他模型的回复，因此新模型能接续共同讨论。`mark_synced` 只是委托更新指定模型的同步位置，不证明模型理解历史，不自动调用 `build`，也不是恰好一次执行保证。

“有界共享上下文”和 token 压缩是产品目标；当前选择器不生成摘要、不运行 tokenizer、不实施严格 token 预算，不承诺压缩率或成本节省。它读取全部历史再筛选，成本随历史和文本长度增长；摘要膨胀、长单条消息、冲突结论与证据遗漏仍需治理。历史被拼成 system 消息也存在提示注入/指令等级混淆风险；未知模型标签可能原值回退，不能直接作为安全公开标签。详细契约见[架构文档](docs/architecture.md)。

## 完整服务设置

### 1. 隔离环境与依赖

建议使用 Python 3.11（项目基础要求 Python 3.9+）、MySQL 8 与 Node.js。Windows 桌面打包脚本另要求 Node.js 22.23.2 x64；源码验证不要求构建安装包。以下命令使用无真实凭据、无业务数据的独立环境。

```powershell
python -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
$env:PYTHON_DOTENV_DISABLED='1'
```

macOS/Linux 使用 `.venv/bin/python`。`setup.bat` / `setup.sh`、`start.bat` / `start.sh` 保留开发入口，不代替配置与安全检查。依赖版本本次未迁移；已知桌面依赖风险和分发限制见 [SECURITY.md](SECURITY.md)。

### 2. 仅服务端配置，通过进程环境注入

[.env.example](.env.example) 是完整变量名称清单，每个赋值均为空，不含可用地址、凭据或启用收费的默认值。`config.py` 不自动读取 `.env`，不能仅复制模板就启动完整服务；也不能把全部空值直接导入：空整数会使 `int("")` 失败，空字符串还可能覆盖安全默认值。仅设置需要的变量，其余不设置以保留代码默认值。

通过受信任的本地启动器或部署的密钥管理功能注入自己新建的隔离配置，不打印配置，不从现有私有 `.env` 复制数据，真实值不纳入版本控制。以下只有本地非秘密设置；凭据由操作者私下提供，不能放进命令历史或提交：

```powershell
$env:PYTHON_DOTENV_DISABLED='1'
$env:MYSQL_HOST='127.0.0.1'
$env:MYSQL_PORT='3306'
$env:MYSQL_USER='astrarelay_dev'
$env:HOST='127.0.0.1'
# 此进程还须获得私下设置的 MYSQL_PASSWORD 与 FLASK_SECRET_KEY。
# 如需注册，再私下配置 SMTP_* 与 EMAIL_CODE_PEPPER。
.\.venv\Scripts\python.exe app.py
```

等价入口是 `python app.py`。完整服务仍使用真实应用工厂、MySQL、模型适配和业务服务，没有被预览替代。先在独立 MySQL 实例创建专用开发用户，授予该实例所需建库、建表、迁移和读写权限；不能给生产数据库执行开发初始化。`Database.initialize` 与 `UserDatabase.initialize` 在启动时自动创建/迁移库表，包含兼容历史模型库；这不是只读启动。

核心变量：`MYSQL_HOST`、`MYSQL_PORT`、`MYSQL_USER`、`MYSQL_PASSWORD`、`MYSQL_DATABASE`、`USER_DATABASE`；`MEMORY_DATABASE` 供独立 Memory 模块使用，不由普通聊天启动自动接入。历史分组/模型库名在 `config.py` 中保留兼容常量，未实现同名环境覆盖。当前不支持 `DATABASE_URL`，它只是未来的发布侧部署契约，不会驱动 MySQL 连接。

`FLASK_SECRET_KEY` 需新生成且至少 32 字符；邮件注册/找回需 `SMTP_HOST`、`SMTP_PORT`、`SMTP_USERNAME`、`SMTP_PASSWORD`、`SMTP_FROM_EMAIL`、`SMTP_USE_TLS` 与独立高熵 `EMAIL_CODE_PEPPER`。未配置邮件时相关操作不可用，不生成公开演示账户。可选短信、管理员、本地模拟支付、网关与限制项的完整解释见[配置表](docs/architecture.md#配置变量与启动行为)。不要添加浏览器端 API Key 流程。

主渠道由 `MODEL_GATEWAY_BASE_URL` / `MODEL_GATEWAY_API_KEY` 设置，兼容和次渠道各有独立通用变量；没有真实内置地址和密钥。正常启动可能触发目录刷新和模型健康检查，配置真实网关前先取得调用授权并确认费用。目录缓存和用户数据不附带，空目录不是模型实现缺失。缺少数据库或会话配置时启动失败是预期行为。

打开 `http://127.0.0.1:5000`：首页 `/`、登录 `/login`、注册 `/register`、目录 `/model-plaza`，登录后使用 `/workspace`。完整账户操作需要有效邮件配置和自行创建的测试账户。管理员环境配置会触发引导，不是普通试运行的默认选项；Windows 专用 `bootstrap_admin.py` 还可能持久化当前用户会话密钥，仅供有权限的操作者单独使用。

`Dockerfile` / `docker-compose.yml` 是开发基础设施示例，不是生产部署：Compose 显式使用私有 `.env` 并暴露端口，Redis 服务不代表已接入分布式队列；Dockerfile 使用 `COPY .`，构建前须确认干净构建上下文，避免打包本地秘密。不要对公网运行 Flask 开发服务器或启用真实支付。

### 3. 可选：无集成界面预览

```powershell
python public_demo.py
```

这是独立附加入口，不改变 `app.py` 正常服务路径。它只监听 `127.0.0.1:5000`，关闭 debug/重载；禁用 dotenv，隔离继承的集成/管理员/支付配置，使用全新空的内存存储与随机会话密钥，不连接 MySQL、模型、邮件、短信或支付，不读私有目录缓存，不启动刷新或健康检查。

可看 `/`、`/login` 和不可用的空目录；不创建账户、不绕过登录，`/workspace` 仍需认证，模型调用保持禁用。重启丢弃内存状态。这不是完整功能演示或真实集成验收。页面仍引用 Tailwind CDN，浏览器断网时样式可能不完整。

### 4. 桌面开发与测试

```powershell
Push-Location desktop
npm ci
npm test
$env:ASTRARELAY_BASE_URL='http://127.0.0.1:5000'
npm start
Pop-Location
```

完整 Flask 服务须另行启动，登录窗口仍做真实认证；预览不能提供已登录桌面演示。`ASTRARELAY_BASE_URL` 是桌面进程变量，不是 Python 模板变量；打包态显式配置的云端地址要求 HTTPS，未配置仍回退本地回环地址，不是正式云端分发配置。开发允许回环 HTTP。渲染层无 Node 权限，cookies/CSRF 由主进程管理；本地根目录须用户选择，受保护文件仍被过滤。签名、更新和完整人工验收没有由自动测试代替，本次不提供二进制发布。

## 回归验证

先在隔离进程设置新生成的临时 `FLASK_SECRET_KEY`（无需输出），保持真实集成凭据未配置，再从仓库根目录运行：

```powershell
$env:PYTHON_DOTENV_DISABLED='1'
python -m pytest -q
node --test tests/test_task_polling.js tests/test_workspace_avatars.js tests/test_reset_password_code.js tests/test_model_plaza.js
Push-Location desktop
npm test
Pop-Location
git diff --check
```

Python 测试覆盖账户、路由/CSRF、数据库、账本、支付模拟、目录、附件、上下文与 Memory；网页测试覆盖轮询、模型显示、找回倒计时与模型广场；桌面测试覆盖信任边界、云端同步、项目操作与渲染契约。默认使用合成数据/注入依赖，不要求真实模型。真实 MySQL Memory 集成测试默认跳过，需隔离实例与显式 opt-in（见[测试脚本](tests/run_memory_mysql_integration.ps1)），绝不能指向业务库。

测试数量随版本变化，以实际输出为准。自动化通过、界面预览、真实模型验收、人工桌面验收、安装包与生产部署是不同证据，不能相互替代。`PYTHON_DOTENV_DISABLED` 不清除已继承的秘密，隔离环境仍然必要。

## 关键取舍与实践规模

共享账本降低上下文搬运成本，也让错误结论传播；多模型一致不是事实正确。纯文本接口兼容旧调用方，却限制结构化使用量与错误表达；SSE 需另立事件契约。先可解释选择，再追求严格压缩，代价是长会话仍有性能和摘要治理债务。局部账本事务不代表模型请求、SSE、回复落库和结算形成跨系统原子操作；重试与幂等要逐层验证。

作者自述：内部项目实践指标曾出现**单日峰值超过 100M tokens（1 亿 tokens）**的用量，未经第三方审计，统计口径和原始计量未在公开仓库独立核验。它不是日均值，不代表并发、吞吐或性能基准，也不证明稳定性、租户数量或收入。项目经历不能用作虚构 benchmark。

## 路线图：从工作台到 AI Team OS

1. 上下文可靠性：严格 token 预算、摘要来源/版本、冲突证据、数据库窗口查询与故障评估。
2. 可审阅编排：分离 Director 的计划、Agent 的受控执行、Memory 的长期信息治理与 Model Router 的能力/成本/可用性策略；保留人的批准、预算与取消。
3. 多租户 SaaS：独立建设租户隔离、成员角色、配额、审计和数据生命周期；多用户账户不是企业多租户证明。
4. 条件式运营：依赖修复、部署安全、桌面签名更新、正规支付、隐私条款与合规评审后再考虑商业服务，不承诺交付日期。

## 维护、贡献与安全

公开分支是完整源码的脱敏对应版本，不自动同步私有开发。每次发布检查范围、敏感内容、测试和最终候选；内部记录、运行数据、环境文件、IDE/Codex 状态与发布维护工具不进入公开快照。

欢迎小范围、可验证改进：先写合成回归，保持旧适配签名、提及解析、路由和迁移兼容，不借重构改变模型选择或启用公众收费。勿提交真实对话、凭据、联系方式或账户记录；公开页面、JSON、日志和报错不应泄露私有接入信息。安全报告见 [SECURITY.md](SECURITY.md)。

面试可继续追问：如何限制摘要增长？如何将归属扩为租户隔离？何时推进同步游标，怎样处理落库失败与重复计费？如何保留历史角色而不提升用户数据的指令等级？如何演进流式/结构化接口又兼容旧调用方？Director 的建议最终由谁授权执行？
