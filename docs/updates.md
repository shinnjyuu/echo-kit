# 更新与任务入口

这些能力从 **0.2.0** 开始提供。使用尚未发布的源码时，先安装本轮 wheel 或源码；不要对已发布的 0.1.x 调用新命令。

AI 每轮从项目入口开始任务，读取该任务对应的 Skill，再执行项目工作。任务开始时检查更新，整轮任务使用同一个工具版本；结束一条命令不会结束任务。

## 一次配置，后续统一调用

已有 `echo-kit.toml` 时，使用支持此机制的 Kit 执行：

```sh
echo-kit --workspace WORKSPACE --json updates setup --policy patch
```

缺少工作区时先 `workspace init`。生成的 `echo-kit.py` 是只依赖 Python 标准库的入口，`echo-kit.lock.json` 保存选定版本与策略。两者由项目管理，应按项目约定纳入 Git。入口文件已有自定义修改时拒绝覆盖；配置、适配器、README、AGENTS 和其他项目文件不自动改写。

在工作区根目录运行：

```sh
python echo-kit.py --json task start --label "验证文件导出修复"
python echo-kit.py --task TASK_ID --json skills show echo-workbench
python echo-kit.py --task TASK_ID --json protocol show
python echo-kit.py --task TASK_ID --json workspace check
python echo-kit.py --task TASK_ID --json verify run CASE
python echo-kit.py --json task finish TASK_ID
```

`TASK_ID` 使用 `task start` 返回的 `id`，`CASE` 使用项目真实案例。命令同时返回可直接使用的 `command_prefix` 和 `finish_command`。Python 需要 3.11+；首次准备其他版本需要 uv 和包源访问。它不会往业务解释器安装依赖。默认全局参数在模块名之前；环境在 `task start` 前用 `--environment NAME` 指定，后续自动沿用。

恢复同一轮工作时用 `task list` / `task show TASK_ID` 查找原任务，再沿用该 ID。任务不会因进程退出或计时过期自动结束。结束时只检查关联的操作和运行，不停止保留复用的服务，不把外部业务状态推断为完成。

## 升级策略与时机

| 策略 | 行为 |
| --- | --- |
| `patch`（新入口默认） | 自动采用同一主版本、次版本内更新的稳定发行版，例如 0.2.0 → 0.2.1 |
| `manual` | 检查并报告新版，项目选定版本保持不变 |
| `latest` | 自动采用支持当前 Python 的最新稳定发行版，包括跨次版本、主版本更新 |

可再次执行 `updates setup --policy POLICY` 修改策略，不改变已开始任务的版本。策略是项目对后续升级的约定，不需要每次补丁发布都再次询问。已有团队明确固定版本时应保留其意图，选择 `manual` 或按已有迁移授权调整。

`task start` 主动刷新发行信息。候选先由 uv 在独立工具环境中准备，核对版本、任务协议、文档摘要，再执行只读的 `workspace check`；成功后原子更新版本文件。当前实现只采用官方 PyPI 的稳定、未撤回且满足当前 Python 要求的版本，不从 GitHub main 读取运行文档，也不自动采用预发行版、私有索引或 Git 开发版本。

候选检查证明包协议和配置可用，不代表项目的业务验收已经通过。`patch` 是允许的版本范围，也不能代替业务断言。下载、协议或配置检查失败时保留旧版，结果中记录 `adoption.state="failed"`，仍可用原版本开始任务；原版本自身不可用时如实受阻。

有任何未结束任务、正在执行的本工作区操作或待清理运行时，自动升级延期，返回原因与相关 ID，本轮沿用原版本。升级准备期间持有工作区版本切换占用，支持此机制的其他业务命令会返回资源占用冲突，不会与切换交叉执行。旧版工具不知道此保护，首次迁移前仍必须收尾旧操作。

显式检查、手动选定版本或回退使用：

```sh
python echo-kit.py --json updates check --refresh
python echo-kit.py --json updates apply --version VERSION
```

`VERSION` 必须替换成真实稳定版本，最低为 0.2.0。显式版本选择仍需先结束任务、处理未完成操作并通过候选检查；会保留 `previous_version`。不指定版本的 `updates apply` 只采用当前策略允许的新鲜候选。它不重启业务服务、不清空锁、不自动重发业务请求。

## 自动检查与离线使用

独立的 `updates check` 不要求工作区。`workspace check`、`doctor`、未绑定任务的 Lab/Verify 也返回更新检查结果。普通检查缓存一小时，失败检查限频五分钟；`--refresh` 和新任务开始会主动刷新。HTTP 检查采用短超时，网络失败不会把正常业务检查改成失败。

全局 `--offline` 或环境变量 `ECHO_KIT_OFFLINE=1` 禁止更新联网，`UV_OFFLINE=1` 也会被识别；已有信息标明离线/过期，不能据此声称已经是最新版本，也不会自动切换。离线时只有已准备的工具环境可运行。检查记录位于当前用户的 Echo Kit 数据目录下 `updates/`，包含候选版本和时间，不含项目配置或凭据。

`--json` 保持标准 JSON 输出；发现新版不修改业务命令退出码。任务记录保存在 `.echo-kit/tasks/`，记录选定版本、环境、标签、文档摘要和检查时间。运行记录与 HTML 报告新增 `tool`、`task_id`、`update`，业务仓库的 `versions` 含义保持不变。

## 文档读取与导出副本

`skills list`、`skills show NAME`、`protocol show`、`runtime info` 全部从运行包读取，不联网。使用任务入口读取时，版本与该任务的执行版本相同；文档摘要改变会阻止继续使用该任务，避免源码被改动后沿用旧的任务声明。

`skills export EMPTY_DIR` 仍仅写入空目录，增加 `.echo-kit-skills.json` 记录版本和文件摘要。`skills status EXPORTED_DIR` 返回导出版本、上游变化、本地修改和缺失文件，不修改任何文件。旧副本没有清单时返回 `unverified`。需要更新文件时导出到新的空目录，比较、合并并保留团队规则。AI 可以直接读取 CLI 正文，因此无需为每次工具升级覆盖项目中的 Skill。

## 已有项目迁移

1. 保留旧版本和当前项目修改，使用旧入口收尾正在执行的操作、未完成运行和外部任务。
2. 安装支持此机制的 Kit，读取它的 `echo-init` 与协议；仅升级全局安装不能改变固定版本的旧命令。
3. 在目标工作区执行 `updates setup`，按团队约定选择策略。生成入口与版本文件，保留业务配置、适配器及已有外部服务。
4. 更新项目已有 AI 指引，使后续工作统一走项目入口，并使用任务 ID；移除用作活动入口的旧版本前缀，保留历史验收记录原文。
5. 开始任务、读取同版本文档、运行一次有意义且已授权的最小验证，记录结果后结束任务。提交和推送项目变更遵循已有授权。

发版只提供新工具，不会远程修改所有旧项目。新入口是这次迁移后持续发现与采用更新的基础。
