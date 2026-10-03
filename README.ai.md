# Echo Kit：AI 接入指南

本指南面向收到“为当前项目接入 Echo Kit”任务的 AI 编程助手。产品用途和可复制的首次使用提示词见 [README](README.md)。接入目标是为用户项目留下可复用的配置、适配器、使用入口和一次有证据的最小验证。

官方仓库：[shinnjyuu/echo-kit](https://github.com/shinnjyuu/echo-kit)。Python 包：[shinnjyuu-echo-kit](https://pypi.org/project/shinnjyuu-echo-kit/)。安装包名为 `shinnjyuu-echo-kit`，命令名为 `echo-kit`。

## 1. 识别目标项目和已有能力

先阅读目标项目的开发约定、README、构建与启动配置、测试和 CI 文件。确认用户要接入的项目路径，避免把 Echo Kit 自身的源码目录当作目标项目。

检查是否已有 `echo-kit.toml`、`echo/`、本机覆盖配置和导出的 Skills。有则按需补齐，保留团队修改。优先复用现有命令、测试与认证流程；只询问无法从项目中确定、且影响接入的事项，例如目标环境、账号和允许操作的范围。

接入可先完成本地配置和最小验证。提交、推送、发布、部署及操作共享业务环境，需要相应任务授权。

## 2. 获取工具并建立项目入口

不要假设本机已安装 Echo Kit、Python 或 uv。检查已有工具及团队规定的 Kit 版本。Kit 需要 Python 3.11+；业务语言不限，业务依赖由目标项目管理。

缺少 uv 时，按 [uv 官方安装说明](https://docs.astral.sh/uv/getting-started/installation/)选择当前平台的安装方式。缺少兼容 Python 时，可通过 `uv python install 3.12` 准备工具运行时。遵循当前机器的安装权限，不改变项目业务解释器。

下文的更新与任务入口要求 **Echo Kit 0.2.0 及以上**。维护分支尚未发版时，从源码或本轮构建的 wheel 安装验证，不能假定 PyPI 最新版已提供这些命令。全新接入可以先查询当前发行版本：

```sh
uvx --from shinnjyuu-echo-kit@latest echo-kit --version
```

拿到符合要求的版本后，用其完成一次安装。`VERSION` 是实际版本号的占位符：

```sh
uv tool install "shinnjyuu-echo-kit==VERSION"
echo-kit --version
echo-kit --help
```

如果已有其他版本、无法修改工具安装或命令不在 PATH，可使用固定版本的独立调用：

```sh
uvx --from shinnjyuu-echo-kit@VERSION echo-kit --help
```

采用独立调用时，初始化阶段的 `echo-kit` 使用这个版本前缀。配置好第 4 节的项目入口后，后续任务统一使用 `python echo-kit.py`，无需在各文档和命令中重复写版本号。

需要从源码安装时，可以使用官方仓库的指定版本，例如：

```sh
uv tool install "git+https://github.com/shinnjyuu/echo-kit.git@vVERSION"
```

源码安装需要 Git；也支持从本地 Kit 源码目录执行 `uv tool install .`，或安装已取得的 wheel。记录来源、版本，源码安装还应记录提交。无法获取工具时说明具体阻碍，保留已完成的项目检查结果。

已有 0.1.x 固定入口的项目需要一次迁移：先按旧入口收尾操作，再安装支持新入口的版本。保留已有团队版本约定，按本轮迁移授权更新入口；不要只升级全局安装后宣称旧的 `uvx ...==0.1.x` 调用已经升级。迁移步骤见[更新机制](docs/updates.md#已有项目迁移)。

## 3. 读取所选版本的 Skills

直接通过 CLI 读取当前包内文档，无需工作区或网络。下文的 `EMPTY_SKILLS_DIR`、`WORKSPACE`、`TASK_ID`、`CASE`、`RUN_ID` 都是占位符，执行前替换为真实值。

```sh
echo-kit --json skills list
echo-kit --json skills show echo-init
echo-kit --json protocol show
```

JSON 包含工具版本、文档摘要和正文。任务开始后，改用 `python echo-kit.py --task TASK_ID --json skills show NAME` 等入口，确保读取的是任务锁定版本。需要文件供助手发现时，也可导出：

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

先阅读导出的 `echo-init/SKILL.md` 和 `references/protocol.md`，再配置工作区或编写适配器。无需启用所有能力；Lab 和 Workbench 可以独立使用。

Skills 的安装位置遵循当前 AI 助手与目标项目的约定，导出命令不会修改助手配置。如果目标 Skills 目录已有内容，先导出到单独的空目录，再检查差异、保留已有修改并合并所需文件。保持各 Skill 与 `references/protocol.md` 的相对路径关系，不能只复制一个 `SKILL.md`。

用 `echo-kit --json skills status EMPTY_SKILLS_DIR` 检查已有导出目录的版本和本地修改。旧版导出缺少清单时会返回 `unverified`，不会覆盖文件。仓库推送、项目选定版本和导出副本分别更新；项目副本仍需比较、合并。不要把仓库最新说明当成旧版 CLI 已支持的行为。

## 4. 配置工作区与最小用例

选择工作区路径，前端、后端和 QA 可以分别位于其他目录。仅在工作区没有 `echo-kit.toml` 时初始化：

```sh
echo-kit --workspace "WORKSPACE" workspace init
```

初始化只建立配置骨架、`echo/` 目录和忽略规则，还需要接入具体用例。根据项目实际情况选一个小而有价值的验证目标，例如已有纯函数测试，或一个本地 API 的响应内容检查。

在目标工作区配置统一入口：

```sh
echo-kit --workspace "WORKSPACE" --json updates setup --policy patch
```

它生成应纳入项目版本管理的 `echo-kit.py` 和 `echo-kit.lock.json`。后者是项目版本与更新策略的唯一来源。`patch` 在新任务开始时允许同一主、次版本内的稳定更新；严格固定版本的团队使用 `manual`，明确接受跨次版本更新时才选择 `latest`。已有入口被团队修改时会保留并报告冲突。该命令不重写项目的 README、AGENTS 或适配器。

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

在工作区根目录开始一轮任务；使用环境覆盖时在 `task` 前加 `--environment NAME`：

```sh
python echo-kit.py --json task start --label "首次接入验证"
```

命令会检查发行版本，按项目策略准备和验证候选，再锁定本轮版本、环境和文档摘要。返回的 `id` 即 `TASK_ID`，也会给出可直接使用的 `command_prefix`。有未结束任务或待收尾操作时延期升级；联网或候选检查失败会保留已选版本。候选检查只覆盖包协议和工作区配置，业务用例仍需下面的实际验证。

本轮所有工作命令使用同一个 `--task TASK_ID`。不要因一条 CLI 命令结束就新建任务，不要在同一轮反复使用 `latest`。恢复会话时用 `task list` / `task show TASK_ID` 找回原任务。先检查配置和资源占用：

```sh
python echo-kit.py --task TASK_ID --json workspace check
python echo-kit.py --task TASK_ID --json operations list
python echo-kit.py --task TASK_ID --json services status
```

对于本地实验，执行：

```sh
python echo-kit.py --task TASK_ID --json lab run CASE --repeat 1
```

对于需要准备服务、认证或浏览器的验收，改用：

```sh
python echo-kit.py --task TASK_ID --json verify run CASE
```

Lab 不自动准备服务、登录或浏览器；Verify 只准备当前用例声明的能力。Kit 不会在用例结束时主动停止托管服务，以便后续复用；跨宿主退出的行为遵循下方的生命周期约定。默认运行一次，仅在实验目的或用户要求需要时增加次数。

也可按需独立使用 `services up NAME`、`auth login NAME` 和 `browser up`；`doctor` 可检查已配置服务的就绪状态。用例通过与否仍以实际检查证据为准。

从结果取得运行 ID，读取记录和报告：

```sh
python echo-kit.py --task TASK_ID --json runs show RUN_ID
python echo-kit.py --task TASK_ID --json runs report RUN_ID
```

默认输出在 `.echo-kit/runs/`，可通过配置的 `output` 改变。记录包含工具版本、文档摘要、任务 ID 和任务开始时的更新检查结果。检查实际执行的范围、必需检查和产物；配置检查通过不能作为业务验收通过的证据。退出码 0 表示已执行范围成功，1 表示检查失败，2 表示受阻或未验证，130 表示中断。`services status` 成功只说明查询成功，还需看每个实例的 `ready`。

需要比较结果时使用 `lab compare CASE --variants baseline candidate --repeat 3` 或 `runs compare LEFT RIGHT`，变体和运行 ID 必须来自项目实际配置或记录。HTTP 成功、页面显示、文件下载和文件内容正确是不同的检查；报告中分别说明。

## 6. 处理占用、认证和未完成任务

**设计允许的生命周期边界**：AI 工具或启动终端退出、更新、重启或崩溃后，托管的本地服务可能停止，也可能继续运行。Kit 不保证跨宿主退出存活，不提供独立守护进程、自动重启或开机恢复。登记文件仍存在不能作为服务存活的证据，也不能把退出宿主视为已经清理服务。完整约定见[公开协议](docs/protocol.md#managed-service-lifetime)。

恢复工作时，先在目标工作区和环境下检查 `operations list`、`services status`。复用仍然就绪的实例；已停止且本轮需要的服务，在处理关联的未完成任务后，按现有授权通过 `services up NAME` 启动。不得因宿主退出就重发业务请求、删除占用记录或接管未知进程；远端或异步业务任务仍需单独确认终态。

同机同系统用户的操作通过共享记录协调。发生 `resource_busy` 时，读取返回的占用者及 `operations show ID`，说明冲突；不强制重启、不删除锁或操作记录。不同资源可以并行，没有自动排队。跨机器、不同系统用户和 Echo 之外的操作不在协调范围内。

外部实例只连接和检查，不接管或停止未知进程。构建类服务可以声明 `artifacts` 记录启动产物哈希；报告需区分当前源码与实际启动版本，热更新服务不承诺固定版本。

异步业务任务按协议记录本轮任务 ID，并配置只针对这些 ID 的清理适配器。超时或中断后不自动重发。未确认终态时保留证据，使用原工作区、原 environment 的 `runs cleanup RUN_ID`；不得删除 `active.json` 伪造完成。

仅在用户明确确认外部任务收尾，且记录的执行进程均已退出后，才可使用 `operations resolve ID --note "确认说明"`。人工解除不会杀进程或清空历史，清理成功也不改变原验收结论。无关联信息的旧记录须先收尾；升级前结束旧版本任务。

认证态只向受支持的系统凭据后端持久化。系统凭据后端不可用时，同次 Verify 调用仍可使用登录态，独立 `auth login` 无法为下一条命令保存状态。Kit 不绕过正常认证；适配器是项目受信代码，需避免把秘密写入日志、结果或截图。报告只自动链接显式声明的产物，分享前检查业务数据。

## 7. 留下后续入口并交付

在目标项目已有的 AI 指引或使用文档中补充入口，保留原有内容。记录选定版本及调用方式、工作区、已接入的用例、Skills 位置、报告位置，以及环境和认证的必要前提。后续会话应能据此复用现有配置。

留下 `python echo-kit.py` 入口与“开始任务、沿用任务 ID、结束任务”的工作方式，版本号由 `echo-kit.lock.json` 维护。工作和关联收尾完成后执行：

```sh
python echo-kit.py --json task finish TASK_ID
```

有关联的未完成运行或仍在执行的命令时会拒绝结束。任务结束不停止为后续复用而保留的服务。不完整的任务保留 ID 交接，不删除任务文件来解锁升级。提交入口、版本文件或策略变更仍遵循本轮已有授权。

完成时分别说明：

- **已配置**：改了哪些文件，接入了哪些能力。
- **已验证**：实际执行的命令、检查范围、运行 ID 和证据位置；区分模拟、真实和混合执行。
- **尚未验证或受阻**：原因及缺少的条件，不能用弱化断言来制造通过。
- **后续用法**：给用户可直接发给 AI 的任务示例，并说明仍在运行的托管服务或未完成任务。

## 示例、验证和维护

独立体验见[演示步骤](docs/demo.md)。核心测试使用 `uv run pytest`，真实示例和 Docker 验收是独立证据。已有[验证记录](docs/validation.md)与[共享操作验证](docs/operations-validation.md)覆盖 Windows 和 Linux 容器，macOS 未验证；不能把容器结果扩展为所有 Linux 桌面或企业认证已验证。

当前不提供 IDE 插件、Agent 引擎、通用验证码绕过或后台自动恢复。回放指已有记录和 Playwright trace 查看，不承诺确定性重跑。

维护 Kit 时，保持 Skill、协议副本与 CLI 文档入口、相关命令和示例一致。随包 Skill 或协议的修改需要提升版本并发布，且应在仓库外验证安装包实际携带的新内容；尚未发布时明确标记“待发版”。项目导出副本需另外比较、合并。完整规则见[发布与随包文档更新机制](docs/publishing.md)，发布遵循本轮已有授权。
