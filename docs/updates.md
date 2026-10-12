# 默认最新版与旧入口迁移

从 **0.4.0** 起，直接使用 `echo-kit`。不再创建项目版本文件、选择升级策略或管理版本任务。新项目只需安装工具、初始化工作区并接入所需能力。

## 命令如何选择版本

普通命令启动时，先查询官方 PyPI 中支持当前 Python、未撤回的最新稳定版。存在新版时，通过 uv 在独立工具环境中准备，确认包能启动且报告正确版本，然后执行原始命令一次。

这不是业务配置验证，也不替代用例断言。工具不会先启动业务服务、登录或跑一次业务命令来测试新版。版本只在当前命令执行前选择；命令运行期间不会切换。不同命令可以使用不同的新版本，运行记录保留实际工具版本。

```sh
echo-kit --workspace WORKSPACE --json workspace check
echo-kit --workspace WORKSPACE --json lab run CASE
echo-kit --workspace WORKSPACE --json docs list
```

`WORKSPACE` 和 `CASE` 替换为实际值。业务配置、适配器、输入输出协议由 Kit 维护兼容；确需变更时提供具体迁移提示，不要求所有项目先管理版本生命周期。

以下诊断命令只查看当前入口，不自动转交新版；`docs --help` 等子命令帮助走普通更新路由：

```sh
echo-kit --version
echo-kit --help
echo-kit --json runtime info
echo-kit --json updates check
echo-kit --json updates check --refresh
```

`--version` 报告当前安装入口的版本，不代表下一条普通命令一定使用该版本。默认检查每次刷新成功的发行信息；网络失败后限频五分钟，`--refresh` 可绕过失败限频。不再用一小时成功缓存推迟更新。

## 失败和离线

并发检查最多等待 3 秒并重读结果，同一轮已完成的检查会被复用；没有长期成功缓存。查询超时、网络失败或新版准备失败时，优先离线验证并使用已知的较新版本，全部不可用才回退安装入口。最近成功准备版本按 Python 解释器记录在用户缓存，原子更新，不保存任意执行路径、不扫描 uv 缓存。更新提示写入 stderr，不污染 stdout 的 JSON。没有可用工具入口时按安装指引准备环境，不能凭缓存信息声称工具已安装。

```sh
echo-kit --offline --json skills show echo-init
echo-kit --workspace WORKSPACE --offline --json lab run CASE
```

`--offline`、`ECHO_KIT_OFFLINE=1` 或 `UV_OFFLINE=1` 禁止更新联网，并明确标记未确认最新版。离线回退使用当前入口，不遍历缓存挑选未知版本。

**业务命令开始执行后，失败、中断或非零退出都不会触发旧版重跑。** 退出码和输出直接返回，避免重复业务请求。最新版准备过程与业务执行分开，准备完成后用已经准备好的包执行；不改变全局安装、业务解释器或运行中的服务。

## 已有项目迁移

0.2/0.3 的版本任务机制已移除。旧入口自身仍会固定旧包，因此需要从新安装的命令执行一次迁移，不能继续通过旧入口调用迁移命令。

1. 在原工作区和环境下收尾仍运行的操作、处理 `needs_cleanup` 和未完成运行。旧版本任务记录标记为 open 本身不再阻挡迁移，也无需伪造完成。
2. 安装正式发布的 0.4.0+：`uv tool install --upgrade shinnjyuu-echo-kit`。已有精确版本约束时改用 `uv tool install --upgrade "shinnjyuu-echo-kit>=0.4.0"`；以实际 `echo-kit --version` 为准。源码验证时安装本轮 wheel，不假定未发布包可从 PyPI 获取。
3. 直接执行 `echo-kit --workspace WORKSPACE updates setup`。只识别官方 0.2.0/0.3.0 生成且未修改的入口，先备份到忽略的 `.echo-kit/migrations/`，替换为调用已安装 `echo-kit` 的薄入口，移除旧 `echo-kit.lock.json`。不存在旧入口时不生成任何文件。
4. 自定义或无法识别的入口不覆盖，手动保留团队修改并把旧命令改为直接 `echo-kit`。迁移后的兼容入口要求 PATH 中已安装的 Kit >= 0.4.0，不承担安装工具的职责。
5. 更新项目自己的 README、AGENTS 和导出 Skill，删除 `task start/finish`、`--task`、`--policy` 和 `updates apply` 用法；工具不会自动重写这些项目文件。提交遵循本轮授权。

历史 `.echo-kit/tasks/`、运行记录和证据保留。旧参数和命令返回迁移提示，不再执行第二套任务机制。项目外部服务、业务任务清理和资源冲突保护仍然有效。

## 文档与排障

Skills 和协议从**实际执行的安装包**读取，不从 GitHub main 拉取正文。普通文档命令同样检查最新版；需要检查当前安装内容时显式加 `--offline`。导出的项目 Skill 是独立副本，使用 `skills status` 检查，再导出到空目录比较合并，保留团队修改。

特殊排障可以显式安装某个版本，并使用 `--offline` 执行，避免排障调用又自动转交新版；不再提供项目级固定版本功能。复验指定发行包时同样使用 `--offline`。

## 0.11.1 旧入口修复

旧入口可能因更新锁忙碌而提前退回旧版。仅发布修复包不能改掉旧入口的路由代码，需要一次执行 `uv tool install --upgrade --index-url https://pypi.org/simple shinnjyuu-echo-kit`，再检查 PATH 对应的 `echo-kit --version`。普通命令的运行记录保留实际版本及回退原因；显式离线仍使用当前包。解析失败且更新未完成时会附加升级提示，业务执行开始后绝不换版本重跑。
