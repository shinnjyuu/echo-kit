# Echo Kit：AI 接入指南

本指南面向收到“为当前项目接入 Echo Kit”任务的 AI 编程助手。产品用途和可复制的首次使用提示词见 [README](README.md)。接入目标是为用户项目留下可复用的配置、适配器、使用入口和一次有证据的最小验证。

官方仓库：[shinnjyuu/echo-kit](https://github.com/shinnjyuu/echo-kit)。Python 包：[shinnjyuu-echo-kit](https://pypi.org/project/shinnjyuu-echo-kit/)。安装包名为 `shinnjyuu-echo-kit`，命令名为 `echo-kit`。

## 1. 识别目标项目和已有能力

先阅读目标项目的开发约定、README、构建与启动配置、测试和 CI 文件。确认用户要接入的项目路径，避免把 Echo Kit 自身的源码目录当作目标项目。

检查是否已有 `echo-kit.toml`、`echo/`、本机覆盖配置和导出的 Skills。有则按需补齐，保留团队修改。优先复用现有命令、测试与认证流程；只询问无法从项目中确定、且影响接入的事项，例如目标环境、账号和允许操作的范围。

接入可先完成本地配置和最小验证。提交、推送、发布、部署及操作共享业务环境，需要相应任务授权。

## 2. 获取工具并固定版本

不要假设本机已安装 Echo Kit、Python 或 uv。检查已有工具及团队规定的 Kit 版本。Kit 需要 Python 3.11+；业务语言不限，业务依赖由目标项目管理。

缺少 uv 时，按 [uv 官方安装说明](https://docs.astral.sh/uv/getting-started/installation/)选择当前平台的安装方式。缺少兼容 Python 时，可通过 `uv python install 3.12` 准备工具运行时。遵循当前机器的安装权限，不改变项目业务解释器。

团队已有固定版本时沿用该版本。全新接入且无版本约定时，可以查询当前发行版本：

```sh
uvx --from shinnjyuu-echo-kit@latest echo-kit --version
```

拿到版本号后，后续命令固定使用该版本。以下以 `0.1.1` 为例，实际执行时替换为选定版本：

```sh
uv tool install "shinnjyuu-echo-kit==0.1.1"
echo-kit --version
echo-kit --help
```

如果已有其他版本、无法修改工具安装或命令不在 PATH，可使用固定版本的独立调用：

```sh
uvx --from shinnjyuu-echo-kit@0.1.1 echo-kit --help
```

采用独立调用时，将下文所有 `echo-kit` 命令替换为同一个固定版本前缀。不要在一轮接入或验收中反复解析 `latest`，也不要中途升级正在使用的工具。

需要从源码安装时，可以使用官方仓库的指定版本，例如：

```sh
uv tool install "git+https://github.com/shinnjyuu/echo-kit.git@v0.1.1"
```

源码安装需要 Git；也支持从本地 Kit 源码目录执行 `uv tool install .`，或安装已取得的 wheel。记录来源、版本，源码安装还应记录提交。无法获取工具时说明具体阻碍，保留已完成的项目检查结果。

## 3. 读取并安置所选版本的 Skills

从已选定的工具版本导出 Skills；这样读取到的接入规则和协议与实际运行版本一致。下文的 `EMPTY_SKILLS_DIR`、`WORKSPACE`、`CASE`、`RUN_ID` 都是占位符，执行前替换为实际路径或名称。

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

先阅读导出的 `echo-init/SKILL.md` 和 `references/protocol.md`，再配置工作区或编写适配器。无需启用所有能力；Lab 和 Workbench 可以独立使用。

Skills 的安装位置遵循当前 AI 助手与目标项目的约定，导出命令不会修改助手配置。如果目标 Skills 目录已有内容，先导出到单独的空目录，再检查差异、保留已有修改并合并所需文件。保持各 Skill 与 `references/protocol.md` 的相对路径关系，不能只复制一个 `SKILL.md`。

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

先检查配置和资源占用，再执行选定的用例。全局参数必须置于模块名前；选择已有环境时，在模块名前加 `--environment NAME`，本轮相关命令使用同一个环境：

```sh
echo-kit --workspace "WORKSPACE" --json workspace check
echo-kit --json operations list
echo-kit --workspace "WORKSPACE" --json services status
```

对于本地实验，执行：

```sh
echo-kit --workspace "WORKSPACE" --actor "首次接入验证" --json lab run CASE --repeat 1
```

对于需要准备服务、认证或浏览器的验收，改用：

```sh
echo-kit --workspace "WORKSPACE" --actor "首次接入验证" --json verify run CASE
```

Lab 不自动准备服务、登录或浏览器；Verify 只准备当前用例声明的能力。托管服务在用例结束后保持运行。默认运行一次，仅在实验目的或用户要求需要时增加次数。

也可按需独立使用 `services up NAME`、`auth login NAME` 和 `browser up`；`doctor` 可检查已配置服务的就绪状态。用例通过与否仍以实际检查证据为准。

从结果取得运行 ID，读取记录和报告：

```sh
echo-kit --workspace "WORKSPACE" --json runs show RUN_ID
echo-kit --workspace "WORKSPACE" --json runs report RUN_ID
```

默认输出在 `.echo-kit/runs/`，可通过配置的 `output` 改变。检查实际执行的范围、必需检查和产物；配置检查通过不能作为业务验收通过的证据。退出码 0 表示已执行范围成功，1 表示检查失败，2 表示受阻或未验证，130 表示中断。`services status` 成功只说明查询成功，还需看每个实例的 `ready`。

需要比较结果时使用 `lab compare CASE --variants baseline candidate --repeat 3` 或 `runs compare LEFT RIGHT`，变体和运行 ID 必须来自项目实际配置或记录。HTTP 成功、页面显示、文件下载和文件内容正确是不同的检查；报告中分别说明。

## 6. 处理占用、认证和未完成任务

同机同系统用户的操作通过共享记录协调。发生 `resource_busy` 时，读取返回的占用者及 `operations show ID`，说明冲突；不强制重启、不删除锁或操作记录。不同资源可以并行，没有自动排队。跨机器、不同系统用户和 Echo 之外的操作不在协调范围内。

外部实例只连接和检查，不接管或停止未知进程。构建类服务可以声明 `artifacts` 记录启动产物哈希；报告需区分当前源码与实际启动版本，热更新服务不承诺固定版本。

异步业务任务按协议记录本轮任务 ID，并配置只针对这些 ID 的清理适配器。超时或中断后不自动重发。未确认终态时保留证据，使用原工作区、原 environment 的 `runs cleanup RUN_ID`；不得删除 `active.json` 伪造完成。

仅在用户明确确认外部任务收尾，且记录的执行进程均已退出后，才可使用 `operations resolve ID --note "确认说明"`。人工解除不会杀进程或清空历史，清理成功也不改变原验收结论。无关联信息的旧记录须先收尾；升级前结束旧版本任务。

认证态只向受支持的系统凭据后端持久化。系统凭据后端不可用时，同次 Verify 调用仍可使用登录态，独立 `auth login` 无法为下一条命令保存状态。Kit 不绕过正常认证；适配器是项目受信代码，需避免把秘密写入日志、结果或截图。报告只自动链接显式声明的产物，分享前检查业务数据。

## 7. 留下后续入口并交付

在目标项目已有的 AI 指引或使用文档中补充入口，保留原有内容。记录选定版本及调用方式、工作区、已接入的用例、Skills 位置、报告位置，以及环境和认证的必要前提。后续会话应能据此复用现有配置。

完成时分别说明：

- **已配置**：改了哪些文件，接入了哪些能力。
- **已验证**：实际执行的命令、检查范围、运行 ID 和证据位置；区分模拟、真实和混合执行。
- **尚未验证或受阻**：原因及缺少的条件，不能用弱化断言来制造通过。
- **后续用法**：给用户可直接发给 AI 的任务示例，并说明仍在运行的托管服务或未完成任务。

## 示例、验证和维护

独立体验见[演示步骤](docs/demo.md)。核心测试使用 `uv run pytest`，真实示例和 Docker 验收是独立证据。已有[验证记录](docs/validation.md)与[共享操作验证](docs/operations-validation.md)覆盖 Windows 和 Linux 容器，macOS 未验证；不能把容器结果扩展为所有 Linux 桌面或企业认证已验证。

当前不提供 IDE 插件、Agent 引擎、通用验证码绕过或后台自动恢复。回放指已有记录和 Playwright trace 查看，不承诺确定性重跑。

维护 Kit 时，保持 `docs/protocol.md` 与 `src/echo_kit/skills/references/protocol.md` 同步。构建与发布步骤见[发布维护说明](docs/publishing.md)，发布需明确任务授权。
