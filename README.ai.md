# Echo Kit：AI 接入指南

本指南面向收到“为当前项目接入 Echo Kit”任务的 AI 编程助手。产品用途和可复制的首次使用提示词见 [README](README.md)。接入目标是为用户项目留下可复用的配置、适配器、使用入口和一次有证据的最小验证。

官方仓库：[shinnjyuu/echo-kit](https://github.com/shinnjyuu/echo-kit)。Python 包：[shinnjyuu-echo-kit](https://pypi.org/project/shinnjyuu-echo-kit/)。安装包名为 `shinnjyuu-echo-kit`，命令名为 `echo-kit`。

## 1. 识别目标项目和已有能力

先阅读目标项目的开发约定、README、构建与启动配置、测试和 CI 文件。确认用户要接入的项目路径，避免把 Echo Kit 自身的源码目录当作目标项目。

检查是否已有 `echo-kit.toml`、`echo/`、本机覆盖配置和导出的 Skills。有则按需补齐，保留团队修改。优先复用现有命令、测试与认证流程；只询问无法从项目中确定、且影响接入的事项，例如目标环境、账号和允许操作的范围。

接入可先完成本地配置和最小验证。提交、推送、发布、部署及操作共享业务环境，需要相应任务授权。

## 2. 安装并直接调用工具

不要假设本机已安装 Echo Kit、Python 或 uv。Kit 需要 Python 3.11+，业务依赖由项目自行管理。缺少 uv 时按 [uv 官方安装说明](https://docs.astral.sh/uv/getting-started/installation/)安装；缺少 Python 时可执行 `uv python install 3.12`。

本指南的默认最新版行为要求 **0.4.0+**。维护分支尚未发布时从源码或本轮 wheel 验证，不能假定 PyPI 已提供新能力。正式发行后安装或升级：

```sh
uv tool install --upgrade shinnjyuu-echo-kit
echo-kit --version
echo-kit --help
```

后续直接使用 `echo-kit`。普通命令启动时检查支持当前 Python 的最新稳定版，在 uv 隔离环境中准备并执行，不更改全局安装或业务解释器。离线或准备失败时明确提示并用本次入口的可用版本继续；业务命令执行失败绝不换版重跑。使用 `--offline` 可明确离线运行和排障。

无法安装全局命令时，可以使用 `uvx --refresh-package shinnjyuu-echo-kit --from shinnjyuu-echo-kit@latest echo-kit ...`。不为项目建立版本文件或版本任务。旧 `echo-kit.py` 和固定版本调用需要主动迁移，见[旧入口迁移](docs/updates.md#已有项目迁移)。

## 3. 读取实际运行版本的 Skills

通过 CLI 读取实际运行包内的文档，无需工作区。普通调用先检查最新版；明确离线时加 `--offline`。下文的 `EMPTY_SKILLS_DIR`、`WORKSPACE`、`CASE`、`RUN_ID` 是占位符，执行前替换为真实值。

```sh
echo-kit --json skills list
echo-kit --json guide echo-init
echo-kit updates notes
```

JSON 包含实际工具版本、文档摘要和正文。版本摘要用于说明来源，不再锁住后续调用。项目需要助手发现入口时，优先写指向 `echo-kit guide echo-init` 的简短 Skill，保留项目事实。明确需要完整离线分发时才导出：

```sh
echo-kit skills export "EMPTY_SKILLS_DIR"
```

导出目录必须不存在或为空。导出会包含以下文件：

| 文件 | 使用场景 |
| --- | --- |
| `echo-init/SKILL.md` | 首次接入或维护项目适配 |
| `echo-lab/SKILL.md` | 运行、重复和比较实验 |
| `echo-workbench/SKILL.md` | 管理服务，执行 API 或浏览器验收 |
| `references/protocol.md` | 配置与子进程协议，和仓库 `docs/protocol.md` 同步 |
| `.echo-kit-skills.json` | 导出版本与文件摘要，用于识别旧副本和项目修改 |

先阅读导出的 `echo-init/SKILL.md` 和协议的核心规则，再按操作读取配置、适配器等相关章节。无需启用所有能力；Lab 和 Workbench 可以独立使用。

Skills 的安装位置遵循当前 AI 助手与目标项目的约定，导出命令不会修改助手配置。如果目标 Skills 目录已有内容，先导出到单独的空目录，再检查差异、保留已有修改并合并所需文件。保持各 Skill 与 `references/protocol.md` 的相对路径关系，不能只复制一个 `SKILL.md`。

用 `echo-kit --json skills status EMPTY_SKILLS_DIR` 检查已有导出目录的版本和本地修改。旧版导出缺少清单时会返回 `unverified`，不会覆盖文件。仓库推送、安装包和项目导出副本分别更新；项目副本仍需比较、合并。不要把仓库最新说明当成旧版 CLI 已支持的行为。

## 4. 配置工作区与最小用例

选择工作区路径，前端、后端和 QA 可以分别位于其他目录。仅在工作区没有 `echo-kit.toml` 时初始化：

```sh
echo-kit --workspace "WORKSPACE" workspace init
```

初始化只建立配置骨架、`echo/` 目录和忽略规则，还需要接入具体用例。根据项目实际情况选一个小而有价值的验证目标，例如已有纯函数测试，或一个本地 API 的响应内容检查。

| 需要的能力 | 接入方式 |
| --- | --- |
| 实验与方案对比 | 配置 `cases`、输入和必要的 `variants`，通过 Lab 执行 |
| API 或业务流程验收 | 配置验收用例，仅声明它需要的 `services` 和 `auth` |
| 页面验收 | 按需增加 `browser`，使用托管 Docker 浏览器或兼容的外部端点 |
| 已有外部服务 | 显式声明 `mode="external"`，保留外部所有权 |

共享的 `echo-kit.toml`、`echo/` 适配器和用例应纳入项目版本管理范围；是否提交遵循用户授权。`.echo-kit/` 保存本机配置和运行状态，应忽略。绝对路径放 `.echo-kit/local.toml`，覆盖顺序为共享配置、本机配置、所选 environment；字典合并，数组替换。

命令必须使用字符串数组，不隐式经过 Shell。`{python}` 指 Kit 的解释器；调用业务 Python 时使用项目实际解释器。`env_refs` 用于引用环境变量，凭据不能写进命令参数、`env` 字面量、用例输入或报告。认证通过专用 stdin/stdout 协议传递，详情见所选版本的协议。

适配器由项目维护，核心不导入业务模块。声明有意义的 `required_checks`，按协议返回检查、指标和产物。`protocol="command"` 只提供退出码级别的证据；若复用这种命令，明确它实际覆盖的范围。完整配置和请求／结果字段以[公开协议](docs/protocol.md)及所选版本的导出副本为准。

## 5. 执行最小验证并检查证据

在工作区直接执行命令。环境覆盖通过 `--environment NAME` 指定，不再从版本任务继承。先检查配置和资源占用：

```sh
echo-kit --json workspace check
echo-kit --json operations list
echo-kit --json services status
```

对于本地实验，执行：

```sh
echo-kit --json lab run CASE --repeat 1
```

对于需要准备服务、认证或浏览器的验收，改用：

```sh
echo-kit --json verify run CASE
```

Lab 不自动准备服务、登录或浏览器；Verify 只准备当前用例声明的能力。Kit 不会在用例结束时主动停止托管服务，以便后续复用；跨宿主退出的行为遵循下方的生命周期约定。默认运行一次，仅在实验目的或用户要求需要时增加次数。

也可按需独立使用 `services up NAME`、`auth login NAME` 和 `browser up`；`doctor` 可检查已配置服务的就绪状态。用例通过与否仍以实际检查证据为准。

从结果取得运行 ID，读取记录和报告：

```sh
echo-kit --json runs show RUN_ID
echo-kit --json runs report RUN_ID
```

默认输出在 `.echo-kit/runs/`，可通过配置的 `output` 改变。记录包含实际工具版本、随包文档摘要和本次命令的更新检查结果。检查实际执行的范围、必需检查和产物；配置检查通过不能作为业务验收通过的证据。退出码 0 表示已执行范围成功，1 表示检查失败，2 表示受阻或未验证，130 表示中断。`services status` 成功只说明查询成功，还需看每个实例的 `ready`。

需要比较结果时使用 `lab compare CASE --variants baseline candidate --repeat 3` 或 `runs compare LEFT RIGHT`，变体和运行 ID 必须来自项目实际配置或记录。HTTP 成功、页面显示、文件下载和文件内容正确是不同的检查；报告中分别说明。

## 6. 处理占用、认证和未完成任务

**设计允许的生命周期边界**：AI 工具或启动终端退出、更新、重启或崩溃后，托管的本地服务可能停止，也可能继续运行。Kit 不保证跨宿主退出存活，不提供独立守护进程、自动重启或开机恢复。登记文件仍存在不能作为服务存活的证据，也不能把退出宿主视为已经清理服务。完整约定见[公开协议](docs/protocol.md#managed-service-lifetime)。

恢复工作时，先在目标工作区和环境下检查 `operations list`、`services status`。复用仍然就绪的实例；已停止且本轮需要的服务，在处理关联的未完成任务后，按现有授权通过 `services up NAME` 启动。不得因宿主退出就重发业务请求、删除占用记录或接管未知进程；远端或异步业务任务仍需单独确认终态。

同机同系统用户的操作通过共享记录协调。发生 `resource_busy` 时，读取返回的占用者及 `operations show ID`，说明冲突；不强制重启、不删除锁或操作记录。不同资源可以并行，没有自动排队。跨机器、不同系统用户和 Echo 之外的操作不在协调范围内。

外部实例只连接和检查，不接管或停止未知进程。构建类服务可以声明 `artifacts` 记录启动产物哈希；报告需区分当前源码与实际启动版本，热更新服务不承诺固定版本。

异步业务任务按协议记录本轮任务 ID，并配置只针对这些 ID 的清理适配器。超时或中断后不自动重发。未确认终态时保留证据，使用原工作区、原 environment 的 `runs cleanup RUN_ID`；不得删除 `active.json` 伪造完成。

仅在用户明确确认外部任务收尾，且记录的执行进程均已退出后，才可使用 `operations resolve ID --note "确认说明"`。人工解除不会杀进程或清空历史，清理成功也不改变原验收结论。无关联信息的旧记录须先收尾；迁移旧入口前先完成旧命令及业务清理。

认证态只向受支持的系统凭据后端持久化。系统凭据后端不可用时，同次 Verify 调用仍可使用登录态，独立 `auth login` 无法为下一条命令保存状态。Kit 不绕过正常认证；适配器是项目受信代码，需避免把秘密写入日志、结果或截图。报告只自动链接显式声明的产物，分享前检查业务数据。

## 7. 留下后续入口并交付

Kit >= 0.3.0 提供项目文档分层。先调用 `echo-kit --json docs list`，读取 `docs show shared`，按任务需要显式读取 `docs show private`。未接入时，在已授权的项目接入范围内运行 `docs setup`；已有入口不覆盖。共享入口默认 `echo/README.md`，私有入口默认 `.echo-kit/private/README.md`。可通过共享配置的 `[documents] shared` 和本机配置的 `[documents] private` 指向既有文档，environment 不可改变归属。

共享文档使用相对文件引用并纳入 Git；私有文档允许绝对路径但必须排除 Git。个人说明只能补充本机事实，不能默默覆盖团队规则或扩大授权。共享结论与复现方法应独立可用，不依赖他人拿不到的私有原始材料；私有内容共享前应提炼并清理个人信息。文档修改后运行 `docs check`，按结果修正；它不自动暂存、提交、删除文件或改写历史。初次生成的共享文档未跟踪时会返回 `unverified`，详见[项目文档说明](docs/project-documents.md)。

在目标项目已有的 AI 指引或使用文档中补充入口，保留原有内容。记录实际验证的版本及直接调用方式、工作区、已接入的用例、Skills 位置、报告位置，以及环境和认证的必要前提。后续会话应能据此复用现有配置。

完成时分别说明：

- **已配置**：改了哪些文件，接入了哪些能力。
- **已验证**：实际执行的命令、检查范围、运行 ID 和证据位置；区分模拟、真实和混合执行。
- **尚未验证或受阻**：原因及缺少的条件，不能用弱化断言来制造通过。
- **后续用法**：给用户可直接发给 AI 的任务示例，并说明仍在运行的托管服务或未完成任务。

## 示例、验证和维护

独立体验见[演示步骤](docs/demo.md)。核心测试使用 `uv run pytest`，真实示例和 Docker 验收是独立证据。已有[验证记录](docs/validation.md)与[共享操作验证](docs/operations-validation.md)覆盖 Windows 和 Linux 容器，macOS 未验证；不能把容器结果扩展为所有 Linux 桌面或企业认证已验证。

当前不提供 IDE 插件、Agent 引擎、通用验证码绕过或后台自动恢复。回放指已有记录和 Playwright trace 查看，不承诺确定性重跑。

维护 Kit 时，保持 Skill、协议副本与 CLI 文档入口、相关命令和示例一致。随包 Skill 或协议的修改需要提升版本并发布，且应在仓库外验证安装包实际携带的新内容；尚未发布时明确标记“待发版”。项目导出副本需另外比较、合并。完整规则见[发布与随包文档更新机制](docs/publishing.md)，发布遵循本轮已有授权。


## 并行启动（0.6.0+）

一次调用 `echo-kit services up api worker web`，默认同时启动全部符合依赖条件的服务；不要用多个并行 CLI 命令绕过资源管理。`[service_startup] concurrency` 可显式限制并发数，`services up/restart --jobs 1` 用于串行排障；Verify 使用配置值。已有 `depends` 继续生效，但不要仅因为服务间有业务调用就添加启动依赖。

需要统一构建时，先运行项目自己的构建脚本并确认成功，再调用 Kit；移除对应服务重复的 `prepare`。项目启动脚本保持前台运行，具体顺序由项目维护。读取启动汇总和 `operations show ID` 中的阶段耗时定位瓶颈；部分失败后先检查状态，复用已启动实例，不自动回滚或重试业务请求。完整规则见[公开协议](docs/protocol.md#parallel-service-startup)。


## 可视浏览器（0.6.0+）

托管浏览器默认 `headless=false`，通过 noVNC 提供本机只读观看页。开始页面验收时展示返回的 `viewer_url`；用户要求观看时打开该链接。CLI 默认尝试调用系统浏览器打开一次，不要另外重复弹窗或在用户关闭后强制重开。`browser.auto_open=false` 禁止自动打开，CI 环境默认不打开，显式配置优先。观看页关闭不影响实验；上下文照常收尾并保存 trace，容器留供复用。

`browser.headless=true` 禁用可视组件和观看端口。控制端口默认 19323，观看端口默认 19324，均只监听本机；使用多个工作区时分别配置端口。`browser up/status` 与 Verify 的浏览器记录包含实际模式、连接和观看地址。多个实验共享一个桌面，前台窗口可能切换。没有页面时桌面可能为空，这不代表浏览器失败。

首次启动从固定 Playwright 基础镜像构建随包运行环境，按资源摘要缓存；构建失败不降级，检查返回的构建日志。`browser.image` 可配置同版本镜像源。旧容器、模式或运行资源变化会提示显式 `browser down`/`browser up`；先处理运行占用，不自动中断实验。外部 `browser.endpoint` 仍只连接，可选 `browser.viewer_url` 仅作外部链接，不自动打开，也不保证只读。详见[公开协议](docs/protocol.md#visible-browser)。

## 获取当前指南与更新说明

AI 在新会话首次使用时读取对应指南，实际执行版本变化后重新读取：

```sh
echo-kit guide echo-init       # 项目接入
echo-kit guide echo-lab        # 实验
echo-kit guide echo-workbench  # 联调验证
echo-kit updates notes
echo-kit updates notes --since 0.5.0
```

指南默认返回同一执行包的精简 Skill、核心规则、主题目录、当前更新说明和版本；按需使用 `--topic TOPIC`，`--full` 才返回完整协议和参考资料；支持 `--json`，无需工作区。普通调用检查最新版，`--offline` 读取当前可用包。`--version` 和 `runtime info` 是安装入口的诊断信息，不能代替业务执行版本。自动升级提示不代表项目已适配。

前端调试推荐 **Kit 管理的 Docker 浏览器与只读 noVNC**。Docker 或构建失败时先处理错误，不自动改接外部浏览器。已有 `browser.endpoint` 仍生效，但只是显式兼容例外：Kit 不管理其镜像、显示模式和观看页，也不保证外部观看链接只读。查看 `browser up/status` 的能力信息；要切换时先检查配置来源和运行占用，在授权范围内调整配置后启动托管浏览器，不停止或接管原外部容器。

项目文档只维护启动方式、用例、认证流程、团队规则和环境归属；个人机器信息留在私有文档。项目 Skill 可写成指向 `echo-kit guide echo-workbench` 的短入口。整理旧副本时保留项目事实，再将通用 Kit 用法改为 CLI 入口，不覆盖原文。需要离线分发完整 Skill 时仍可导出到空目录并比较合并。

## Windows 托盘：关了观看页也能找回来

0.8.0 起，在 Windows 桌面启动 Kit 托管服务或浏览器时，会自动出现 Echo Kit 托盘图标（可能收在右下角隐藏图标区）。关闭 noVNC 标签页后，右键托盘，选择项目的“打开调试画面”，即可继续看同一次实验，不用向 AI 发消息。

托盘显示进程与容器状态，不代表业务健康检查通过。无头模式、旧容器没有观看入口；外部浏览器不纳入托管列表。菜单没有停止或重启业务资源的按钮。

```sh
echo-kit tray start           # 当前项目已有资源时，手动显示入口
echo-kit --json tray status   # 无需工作区
echo-kit tray stop            # 只退出托盘，服务和实验继续
```

全部托管资源确认停止后，托盘等待 30 秒自动退出；查询失败显示“状态未知”。手动退出后，同一次操作不会重新拉起，下一次启动操作可以恢复。CI、非交互桌面或环境变量 `ECHO_KIT_TRAY=0` 禁用自动启动；macOS/Linux 继续使用观看链接。

托盘独立运行，但不是服务守护程序，也不开机启动。如果 Windows 宿主限制后台进程独立启动，Kit 提示原因并保留原观看链接，不影响调试结果。已有项目运行一次 `tray start` 或新的启动命令即可接入，不改配置或业务容器。

## 初始化时准备测试身份（0.8.3 起）

先根据本轮用例确认是否需要登录，以及账号、角色、权限和租户需求；匿名用例跳过。检查已有种子、夹具、注册 API、管理命令和准备脚本，优先复用。没有可重复执行入口时，在项目既有脚本目录（没有约定时用 echo/）编写项目语言的脚本，不给 Kit 添加业务账号模型。

按稳定身份和租户查询，存在就核对归属并复用，不存在才通过正常业务入口创建；并发由项目唯一约束或事务防重。同名账号、密码、角色或租户不匹配时报告差异，不自动接管、重置或扩权。目标必须明确且允许：默认本地开发，共享测试环境需要显式配置和已有任务授权，未知目标不执行；已有授权不重复索取。

账号准备保持显式，不塞进 auth login、服务启动或 Verify 的隐式路径。准备后用现有认证适配器验证登录，再做最小的角色/租户检查。结果区分“不需要账号、已复用、已创建并验证、仍待准备”；只有脚本或只有登录成功都不等于业务身份验收通过。

凭据沿用项目秘密注入方式及 Kit 环境变量到私有 stdin 的认证通道；凭据库保存认证状态，不新增账号密码存储。不在参数、用例输入、共享/私有文档或运行报告写秘密，错误也不输出原始认证响应。共享文档只写准备命令、用途、目标、秘密来源名称和清理规则；个人路径放私有说明。

长期复用身份保留。临时创建成功后立即记录本次拥有的准确 ID、目标和 run_id，再执行后续验证；通过项目清理适配器清理，仅删本次创建的账号，失败保留待清理证据。不得按名称前缀删除或删除复用身份。

`guide echo-init` 与 `skills show echo-init` 的 JSON 在原正文之外返回 references，普通输出附带同包参考指南；无需工作区。参考指南包含项目伪代码、脱敏结果和检查项，也随 `skills export EMPTY_DIR` 导出为 references/test-identities.md。所有示例函数均为项目占位逻辑，不是新 CLI；已有文件不覆盖。


## 共享配置与本机差异

修改前先读取 `echo-kit.toml`、`.echo-kit/local.toml` 和当前 environment，判断归属：团队启动命令、项目相对目录、依赖、就绪规则和团队默认端口进入共享配置及 Git；本机避让端口、绝对路径、个人镜像源和本机外部端点进入最小的私有覆盖。进程身份、容器 ID 和实际连接地址由 Kit 登记；密码和令牌走现有秘密注入与认证机制，不写入两类配置。

先合并共享与本机配置，再应用所选 environment；字典递归合并，数组整体替换。本机根级值不一定覆盖共享 environment 的值，需在本机同名 environment 表中覆盖。启动脚本共享并读取项目支持的参数或环境变量，Kit 不会把任意端口字段自动替换进命令。调整服务端口时同时核对启动输入、端口声明、就绪检查及消费地址；浏览器端口变化仍需确认运行占用并显式 down/up，不打断实验。

验证实际解析的非秘密值，按需启动检查，不输出完整配置。检查私有文件既被忽略又未被 Git 跟踪：已有跟踪不会被新增 ignore 规则解除，发现后报告处理建议，不自动删除文件或改写历史。在授权范围内整理混入共享配置的个人值，保留团队默认值和项目修改。私有存储不等于加密。

可复制的成对示例见[公开协议](docs/protocol.md#shared-and-local-configuration)，也可通过 `echo-kit guide echo-init` 或 `echo-kit protocol show` 从执行中的安装包读取。已有导出副本需导出到空目录后比较合并。


## 实验目录与分层读取（0.9.0）

先用 `echo-kit cases list` 浏览分类，或 `cases list --query 登录` 搜索名称、描述与标签，再用 `cases show CASE` 读取选中的说明和声明的前置条件。用例可添加 description/category/tags/docs 元数据，继续复用原有配置和脚本。分类不要求与磁盘目录一致，旧用例仍可发现和运行。

项目入口只维护概况、必要团队规则和目录入口；每个实验独立说明目的、适用条件、准备事项、命令和结论；源码、日志、截图与 trace 需要时再读。修改配置、服务、浏览器、认证或适配器前，读取 `guide NAME --topic TOPIC` 对应主题。默认指南不再附带完整协议和参考资料；`--full` 可完整读取，`skills show` 只返回 Skill 本身，完整导出继续可用。

`runs list --case CASE` 默认只列最近 20 条摘要，使用 `--limit`（1..200）和 `--offset` 翻页。JSON 从完整记录数组改为包含 items、total、next_offset 等字段的对象；选中后用 `runs show ID` 读取完整记录。分页不替代完整的占用和未完成清理检查。列表不读取脚本或日志正文，私有补充仍需显式读取，不自动迁移已有文档。

## 共享实例与独立浏览器（0.10.0）

跨工作区使用微服务前读取 `guide echo-workbench --topic services`。显式 resource_id 表示同一受管实例，不能仅凭端口或服务别名推断共享。先核对解析后的启动输入；个人映射写本机配置。用例默认 shared，占用不隔离业务数据；不能并发的数据修改设 service_access="exclusive"。无参数 down/restart 仅管理当前工作区和环境启动的实例。

旧登记在原工作区完成未结束操作后通过 services up 核验迁移，只读 status 不迁移。所有协作会话使用新版；旧离线入口不参与统一协调。浏览器与观看页仍按工作区独立，本机端口分别配置；托盘只展示，不做停止或重启。

## 文档地图与目录规则（0.11.0）

处理具体模块前先读取 documents 主题，再执行 `docs context PATH`；其范围是整个工作区中的目录链，不限 echo/，也不代表客户端实际加载的全部指令。使用 `docs map --path DIR` 或 `--query TEXT` 定位资料，再 `docs read PATH` 按需读取。未验证或超限时确认缺失范围，不能当作已经读完。

写入前分别判断团队／个人、全项目／局部、工作约束／详细需求。根目录 AGENTS.md 保留通用约束，模块 AGENTS.md 写局部约束与资料入口，原型和业务细节放独立文档。跨模块共用知识只留一份正文。发现放错位置先说明建议，再按授权整理与修复引用；CLI 不自动搬迁，也不自动裁定语义冲突。规则修改后执行 `docs check --rules`，涉及已登记文档入口再执行普通 `docs check`。
