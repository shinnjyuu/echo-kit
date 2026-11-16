# Echo Kit

**简体中文** | [English](README.en.md)

**让 AI 为你的项目做实验、查问题、验收结果，并把这些过程留下来反复使用。**

Echo Kit 是一套供 AI 编程助手调用的工具。它帮助 AI 复用项目的启动和测试流程，执行检查，并留下结果、日志和文件等证据。换一个会话或换一位同事，也能沿用项目里已经整理好的验证方法。

本页面向第一次了解 Echo Kit 的人。负责接入项目的 AI 请阅读 [AI 接入指南](README.ai.md)。

## 1.0：按原型做页面，用实际画面对照

以下为 1.0.0 的能力；发行状态以官方 PyPI 为准。已有项目的浏览器配置需要明确选择后端。

日常前端调试默认使用 Kit 管理的本机有头 Chrome 与 agent-browser。AI 直接读取页面、点击、填写和截图，不必先编写一份控制脚本；你可以直接看到它操作的浏览器。Docker／noVNC 仅在明确选择 `backend = "playwright"` 时启用。

```sh
echo-kit browser up --url http://localhost:5173 --service web
echo-kit browser exec -- snapshot -c
echo-kit browser exec -- click @e1
echo-kit browser exec -- screenshot
```

页面需要按原型还原时，再使用持续观察：

```sh
echo-kit browser observe --url http://localhost:5173 \
  --reference prototypes/dashboard.html --spec docs/dashboard.md
```

HTML／网址提供视觉基准，Markdown 原型或 PRD 提供需求依据。AI 显式采集基准、检查区域尺寸与样式、查看并排图及差异图，修正后继续对照同一基准。原型浏览器默认只在采集时临时打开；普通修改不增加固定脚本或全站矩阵。只给 Markdown 时按条目核对，不宣称像素还原。详见 `echo-kit guide echo-workbench --topic visual`。

升级不自动迁移业务项目。旧浏览器配置需要明确选择原生或兼容后端；旧 Playwright helper、准备回调及 `trace.zip` 仅用于显式兼容方式。原生 trace 是 Chrome/CDP JSON。1.x 的稳定基线从这些明确区分的接口开始，不能把 0.15 的旧浏览器脚本当作无需适配。

## 它能帮我做什么？

比如，你刚让 AI 修复了一个文件导出功能，还想知道：页面能操作吗？文件真的下载了吗？内容对不对？

接入对应的验收用例后，你可以直接告诉 AI：

> 用 Echo Kit 验证这次导出功能的修改，检查页面操作、文件下载和内容，并把结果和证据给我。

AI 就可以复用项目中约定的流程，准备所需服务、执行这些检查，并告诉你哪些通过、哪些失败、哪些还没验证。下次改到同一功能，还能继续使用这套流程。

| 你想做的事 | Echo Kit 提供的支持 |
| --- | --- |
| 比较两种算法、参数或实现 | 分别运行候选方案，保存每次结果，对比检查项和指标 |
| 验收一次功能修改 | 按用例准备服务、登录和浏览器，收集接口或页面检查结果 |
| 让后续 AI 和同事接着用 | 把配置和验证脚本保存在项目里，留下可查看的运行记录 |
| 多个 AI 会话同时工作 | 在同一台机器、同一系统用户下，识别服务等资源的占用冲突 |
| 持续使用新版工具和说明 | 普通命令启动时检查并使用最新稳定版；离线或准备失败继续使用当前可用版（0.4.0 起） |

具体检查什么、怎样判断正确，由项目自己的验证脚本定义；AI 会在首次接入时复用或补充这些脚本。实验、服务管理和功能验收可以按需使用。

## 第一次使用：复制给 AI

**你不需要提前安装 Echo Kit，也不需要先下载这个仓库。** 打开你自己的项目，使用能够联网、读写项目文件并执行命令的 AI 编程助手，把下面整段话发给它。提示词已经包含工具地址。

```text
请为当前项目接入 Echo Kit，让后续 AI 会话能够复用
项目的实验、调试和验收能力。

官方仓库：https://github.com/shinnjyuu/echo-kit
请先阅读仓库根目录的 README.ai.md，按其中的步骤获取工具，
再遵循所选版本的 echo-init Skill 和公开协议完成接入。
不要假设本机已经安装 Echo Kit 或其运行环境；请自行检查，
按官方说明安装所需工具，并记录本次使用的版本。

先了解当前项目的开发约定、目录结构、启动方式和已有测试，
优先复用现有命令与脚本。若已有部分接入，请在原有基础上补齐，
保留团队修改。根据项目实际需要选择能力，
先打通一个有实际价值的最小本地验证流程。

请把可复用的配置、适配器和后续 AI 使用入口留在项目中；
默认直接调用最新版 Echo Kit，无需项目版本文件或任务生命周期；
本机路径和凭据按协议处理。只对无法从项目中确定、
且影响接入的关键事项向我提问。
操作共享业务环境需要另行取得授权，不要提交或推送代码。

完成后说明：接入了什么、实际验证了什么、还有哪些未完成事项、
证据在哪里，以及我以后可以怎样让 AI 使用这些能力。
```

AI 会先检查项目和本机环境，再安装工具、配置所需能力并运行最小验证。你只需补充它无法从项目里确定的信息，例如目标测试环境或登录方式。

## 接入后，我会得到什么？

- **可以重复使用的验证流程**：项目里保存配置和验证脚本，后续会话可以继续调用。
- **有依据的结果**：每次运行留下检查结论和报告；用例还可以保存截图、下载文件等证据。
- **清楚的交接说明**：哪些能力已经可用、哪些确实跑通过、下一次应该怎样使用。

涉及页面或交互改造时，新版指引要求 AI 主动展示关键更新截图，配上位置、变化和操作说明，优先复用本轮验收画面。模拟数据或隔离页面会明确标注；截图展示与行为验证分开说明。纯后端改动用文字交付，不默认画图。截图条件不足时说明原因、可用业务入口及待确认事项，保持原有人工验收方式，不要求为截图新增脚本。详见[页面改造交付规则](docs/protocol.md#page-change-handoff)。

例如，接入相应能力后，你可以继续这样交代 AI：

> 用 Echo Kit 对比当前实现和候选实现，各运行三次，把指标差异给我。

> 用 Echo Kit 跑一下这个项目已有的 API 验收，说明通过、失败和未验证的部分。

> 看一下上次失败的运行记录，结合日志和检查结果定位问题。

项目的业务依赖和开发方式仍由项目管理。前端、后端和验收脚本可以在不同目录，也可以连接已经运行的外部服务。

## 使用条件与当前范围

Echo Kit 本身需要 Python 3.11+，业务项目可以使用其他语言。AI 可以协助安装工具及其运行环境。普通实验、API 验收和默认原生浏览器不要求 Docker；只有明确选择 Playwright 兼容浏览器时才需要 Docker。

**AI 工具退出、更新或崩溃后，托管的本地服务可能停止，这是设计允许的场景。** Echo Kit 支持服务在命令结束后继续复用，但不保证跨宿主退出存活，也不保证随宿主退出自动清理。后续 AI 应先检查状态，再复用或按需启动。详见[服务生命周期约定](docs/protocol.md#managed-service-lifetime)。

原生运行包首版支持 Windows x64、Linux x64；Linux 浏览器验证使用 Xvfb，macOS 和其他架构暂不支持原生后端。登录需要项目正常的认证流程。截图、日志和下载文件可能含业务数据，分享前请检查。

验收结论只覆盖实际执行的检查。Kit 会标明失败、受阻和未验证的情况；历史记录用于追查过程，不保证重跑结果完全一致。

## 项目文档分层

Echo Kit 0.3.0 起支持区分团队共享文档与个人私有文档：共享文档使用相对路径、纳入 Git；个人文档允许本机绝对路径、排除 Git。AI 可通过 `docs list` 发现入口、`docs show` 读取、`docs check` 检查。已有项目主动执行 `docs setup` 接入，保留原有文件。见[项目文档说明](docs/project-documents.md)。

## 进一步阅读

修改页面后，可以用 `echo-kit browser observe --url URL --service web` 保持一个观察会话，连续取得当前画面和简短布局诊断。默认保留登录、弹窗和未保存输入，按需刷新或导航。简单页面直接使用，复杂业务页面先通过受管交互和项目已有入口准备状态；Python 观察适配器仅用于显式 Playwright 兼容模式。详见[快速视觉观察](src/echo_kit/skills/references/visual-observation.md)。

- [AI 接入指南](README.ai.md)：从未安装到完成项目接入，也适合希望手动配置的开发者。
- [独立演示](docs/demo.md)：用示例项目体验实验、API 验收和浏览器验收。
- [公开协议](docs/protocol.md)：编写项目适配器、配置认证和管理运行结果。
- [默认最新版与旧入口迁移](docs/updates.md)：启动检查、离线回退和文档同步。
- [验证记录](docs/validation.md)与[多会话协作验证](docs/operations-validation.md)：已验证的范围和平台限制。
- [发布维护说明](docs/publishing.md)：面向维护者的构建和发布流程。


## 查看资源用量

`echo-kit resources status` 查看当前工作区服务和托管浏览器的内存、CPU、引用工作区及当前占用者；`resources status api worker` 只查指定服务。`echo-kit --json resources status --all` 可在任意目录查询所有已知工作区，不加载业务配置；不要与工作区、环境或服务筛选混用。列表默认按内存降序，每页 20 项，使用 `--limit`、`--offset` 分页，汇总覆盖完整查询范围。

Windows 托盘增加只读资源摘要、内存最高的三个服务及各服务／浏览器的用量。约每五秒更新，超过十五秒标记旧数据；安装新版后重新执行 `tray stop`、`tray start` 载入菜单。CLI 在 Linux 也可用。

本机服务使用包含子进程的 RSS／Working Set 观测值，共享实例去重；容器按 Docker 口径单列。合计不代表整机占用或停止后一定能释放的内存，未知指标不会当成零。查询不跑业务健康检查，也不自动停止服务。详见[资源用量协议](docs/protocol.md#resource-usage)。

## 按需回收磁盘产物

`echo-kit resources disk` 查看运行附件、已结束观察会话和已声明临时目录的磁盘账单；加 `--all` 查询所有已知工作区。`resources clean` **只生成预览**，取得计划编号后用 `resources clean --apply PLAN_ID` 执行。可用 `--root ROOT_ID` 缩小预览、`--unit UNIT_ID` 选择计划中的部分条目，`resources keep UNIT_ID` 标记重要证据。

默认保留近期和最近几次结果、运行主记录与简短报告。活跃任务、待业务清理、Git 跟踪和归属不明内容会受保护；预览后有变化会跳过。项目临时目录需通过根配置或本机配置的 `[storage.caches.NAME]` 显式登记，个人路径和保留偏好放 `.echo-kit/local.toml`。不后台自动删除，不清理 Docker 镜像；预计逻辑体积不等于实际腾出的空间。详见[磁盘回收规则与配置](docs/protocol.md#disk-reclamation)。

## 多个服务一起启动

0.6.0 起，`echo-kit services up` 默认同时启动所有符合依赖条件的服务，无需让 AI 一条一条执行。已有服务会复用，失败会保留已启动服务并报告原因。`services up --jobs 1` 可串行排障，资源有限时可通过 `[service_startup] concurrency = 4` 显式限制并发数。

统一构建、特殊启动顺序由项目脚本负责；Kit 管理各服务进程、等待就绪并记录耗时。详见[启动约定](docs/protocol.md#parallel-service-startup)。


## 看见 AI 正在调试的页面

1.0 默认直接显示本机独立 Chrome 窗口，不需要 Docker 或 noVNC。Kit 准备固定并校验摘要的 agent-browser 与 Chrome 运行包，不使用个人日常浏览器的登录状态。首次下载比热态操作慢；显式离线只复用已经准备的运行包。

```toml
[browser]
backend = "agent-browser"  # 默认；明确写 playwright 才启用旧方式
headless = false          # 默认直接显示浏览器窗口
```

原生窗口可以被你操作或关闭；关闭正在调试的页面会影响该会话，未保存状态不能保证恢复。Kit 不会偷偷创建新页面并重跑动作。通过状态和托盘定位所属窗口；窗口已结束时明确报告。`browser down` 只结束指定所属会话，服务继续保留。

兼容项目可明确使用 `echo-kit browser up --backend playwright` 或相应配置，继续采用 Docker／只读 noVNC。旧端口、镜像、endpoint 或已有容器不会自动选择该方式；切换前先处理旧占用并显式停止旧实例。原生失败不静默回退。旧 Python helper 按需读取 `guide echo-workbench --topic browser-compatibility`，日常使用 `browser` 主题。详见[可视浏览器约定](docs/protocol.md#visible-browser)。

## 获取当前指南与更新说明

AI 在新会话首次使用时读取对应指南，实际执行版本变化后重新读取：

```sh
echo-kit guide echo-init       # 项目接入
echo-kit guide echo-lab        # 实验
echo-kit guide echo-workbench  # 联调验证
echo-kit updates notes
echo-kit updates notes --since 0.5.0
```

指南默认返回同一执行包的精简 Skill、核心规则、主题目录、当前更新说明和版本；按需使用 `--topic TOPIC`，`--full` 才返回完整协议和参考资料；支持 `--json`，无需工作区。日常调用检查最新版；显式 `--offline` 和离线环境变量停留在当前入口，只用于确实离线或指定安装包验收。`--version` 按普通更新逻辑显示本次实际执行版本；`runtime info` 只诊断本地入口。一次实验的版本以运行记录为准。自动升级提示不代表项目已适配。

前端调试默认使用 **受管原生有头浏览器与 agent-browser**。旧 Docker／noVNC 和外部 Playwright endpoint 只在明确选择兼容后端时使用，不自动发现或接管外部实例。旧配置需要查看来源并作出选择；原生失败不自动回退。切换前检查运行占用和实际登记，不因配置改变而误停原实例。

项目文档只维护启动方式、用例、认证流程、团队规则和环境归属；个人机器信息留在私有文档。项目 Skill 可写成指向 `echo-kit guide echo-workbench` 的短入口。整理旧副本时保留项目事实，再将通用 Kit 用法改为 CLI 入口，不覆盖原文。需要离线分发完整 Skill 时仍可导出到空目录并比较合并。

## Windows 托盘：关了观看页也能找回来

在 Windows 桌面启动受管服务、原生浏览器或本地构建／实验时，会出现 Echo Kit 托盘图标。选择所属工作区和会话，可定位正在运行的原生 Chrome 窗口；兼容 Docker 实例继续打开原 noVNC 链接。状态来自真实进程身份及容器归属，不执行项目健康检查。

```sh
echo-kit tray start
echo-kit --json tray status
echo-kit tray stop  # 只退出托盘，业务资源继续
```

原生窗口已经关闭时，不通过新建页面冒充恢复；未保存状态不能保证找回。托盘只提供入口，不提供后台业务守护或开机启动。全部资源确认停止后等待 30 秒退出；未知状态不会当作已停止。CI、非交互桌面和 `ECHO_KIT_TRAY=0` 禁用自动托盘。受限 Windows 宿主可能无法让后台进程跨宿主退出存活，需如实说明。

**急停所有工作区** 同时覆盖所属原生协调进程、agent-browser、Chrome、旧调试容器、服务和任务。也可明确让 AI 执行 `echo-kit emergency stop --all`；不保存业务数据或运行清理脚本，未确认结果保留证据。安装新版后重新开启托盘载入菜单。详见[急停协议](docs/protocol.md#emergency-stop)。

## 测试账号也要准备好

初始化时，AI 会先判断用例是否需要登录，优先复用项目已有测试账号、种子数据或注册/管理入口；缺少可重复执行的入口时，再编写项目自己的准备脚本。账号准备是显式步骤，不会藏在登录或启动命令里。

准备后分别验证登录和用例所需的角色、租户权限。已有账号不擅自重置密码或扩权；无需登录的用例直接跳过。长期测试身份保留，临时身份只清理本次创建的记录。完整方法见 `echo-kit guide echo-init --topic auth`；这是随包指导，不是 Kit 自动创建账号的新功能。


团队共享启动命令、相对路径和默认设置；本机端口冲突、绝对路径等差异放在不提交的 `.echo-kit/local.toml`。AI 会核对环境覆盖和关联检查，保留团队启动方式。参见[配置分离示例](docs/protocol.md#shared-and-local-configuration)。


## 实验目录与分层读取（0.9.0）

先用 `echo-kit cases list` 浏览分类，或 `cases list --query 登录` 搜索名称、描述与标签，再用 `cases show CASE` 读取选中的说明和声明的前置条件。用例可添加 description/category/tags/docs 元数据，继续复用原有配置和脚本。分类不要求与磁盘目录一致，旧用例仍可发现和运行。

项目入口只维护概况、必要团队规则和目录入口；每个实验独立说明目的、适用条件、准备事项、命令和结论；源码、日志、截图与 trace 需要时再读。修改配置、服务、浏览器、认证或适配器前，读取 `guide NAME --topic TOPIC` 对应主题。默认指南不再附带完整协议和参考资料；`--full` 可完整读取，`skills show` 只返回 Skill 本身，完整导出继续可用。

`runs list --case CASE` 默认只列最近 20 条摘要，使用 `--limit`（1..200）和 `--offset` 翻页。JSON 从完整记录数组改为包含 items、total、next_offset 等字段的对象；选中后用 `runs show ID` 读取完整记录。分页不替代完整的占用和未完成清理检查。列表不读取脚本或日志正文，私有补充仍需显式读取，不自动迁移已有文档。

## 多工作区共享服务

0.10.0 起，相同显式 `services.NAME.resource_id` 可以跨工作区复用同一个受管微服务；启动配置必须相容。未指定 ID 时仍独立。实验默认共享使用，需要避免并发修改数据时设置 `service_access = "exclusive"`。服务使用中不能停止或重启。

浏览器仍按工作区独立，端口避让写本机 `.echo-kit/local.toml`。托盘分别显示共享微服务和各项目观看入口。通过 `echo-kit guide echo-workbench --topic services` 读取接入、旧登记迁移和停止范围说明。

## 找到模块文档，写对规则位置

`echo-kit docs map` 发现整个工作区的 Markdown，不局限于 echo/。修改模块前用 `docs context 模块目录` 读取沿途 AGENTS.md，再用 `docs read 文档路径` 按需读取说明。根目录保留全项目约束，子目录维护局部约束，详细需求独立成文并链接。

`docs check --rules` 检查规则文件及直接引用，不自动搬迁文档，也不判断业务语义是否冲突。个人材料仍通过私有入口显式读取。详细边界见 `guide echo-init --topic documents`。

自动更新并发修复（0.11.1）：检查锁忙碌时短暂等待并复用结果，网络失败时优先使用已验证的较新缓存版本。子命令帮助跟随实际路由；显式 `--offline` 保持当前包。旧安装入口需升级一次才能获得修复，见[更新说明](docs/updates.md#0111-旧入口修复)。

模块配置（0.12.0）：根 `echo-kit.toml` 可通过 `includes = ["services/orders/echo.toml"]` 显式加载模块的实验、服务、认证和项目配置。相对路径含义不变，本机差异仍写 `.echo-kit/local.toml`。用 `workspace check` 查看文件清单，`cases show ID` 查看用例配置来源；小项目无需拆分。详见[配置拆分](docs/modular-configuration.md)。

版本查询（0.12.3）：日常使用 `echo-kit --version`，成功时只输出版本号。离线检查指定安装使用 `echo-kit --offline --version`；排障使用 `echo-kit --json runtime info`（version_scope 为 installed_entry）。旧入口需升级一次才能获得新的查询行为，见[更新说明](docs/updates.md#0123-版本查询)。

失效占用：AI 检查操作与登记子进程已退出后，可直接用 `operations resolve ID --note ...` 解除已确认失效的占用，无需单独人工确认；记录检查依据和原因。解除不停止服务、不删除数据，也不代表业务已清理。需要时使用现有清理适配器；远端任务仍在执行或状态无法判断时不能仅凭本地退出认定完成。已有导出指南不会自动覆盖，请导出到空目录后比较合并。

验收按需选择：你自己验收时，AI 准备服务并给出业务前端地址；让 AI 验收时，优先直接交互观察，不默认生成固定脚本或用例配置；需要重复验证、精确时序或回归断言时才固化实验。noVNC 仍只供观看。交互能力与宿主工具有关，无法连接时应如实说明，不能把启动浏览器当成验收通过。

先复用已有启动、登录和验收入口，不因使用 Kit 重搭整套环境。完整页面流程前，用实际脚本客户端核对 `browser up/status` 的 `required_playwright_version`，依次做最小连接、目标身份和必要登录检查，失败先修复该点。页面流程可用明确标注的受控模型回复；真实模型质量另行验证。模型实验先以少量样本确认协议、单位、引用和保存流程，再扩大规模，并报告请求、重试、耗时及用量。

查询当前工作用 `echo-kit operations list --unfinished`；原 `operations list` 继续保留全部历史。`services status NAME...` 只检查指定服务。内部查询跳过已完成且无占用历史的进程检查，一次查询复用资源匹配和源码指纹，不删除历史或削弱占用保护。日常施工、指南读取使用普通命令；`--offline` 和离线环境变量会停留在当前入口，仅用于确实离线或指定包验收。

运行时排障按需使用：逻辑问题检查实际变量、调用栈和分支；时序问题优先记录不停顿的关联事件。通过 services/browser/cases 指南主题读取 Python、Node 隔离示例；已安装调试器不等于已经定位问题。不新增调试 CLI 或必装运行时依赖。
