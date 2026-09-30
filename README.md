# Echo Kit

让 AI 的实验、调试与验收成为团队可复用的工程能力。

发布维护见 [PyPI 发布说明](docs/publishing.md)。首次正式发布完成后可使用 `uvx echo-kit@latest --version` 获取当前版本，再通过 `uvx echo-kit@具体版本 …` 固定一轮任务的执行版本。

Python 3.11+。一个包提供 workspace、doctor、services、auth、browser、lab、verify、runs 和 Skill 导出。各能力按需使用，Lab 不要求 Docker 或服务启动。

```powershell
uv tool install .
echo-kit --help
echo-kit --workspace D:/my-project workspace init
echo-kit skills export D:/my-project/.agents/skills
```

也可以 `uv tool install git+<仓库地址>` 或安装 `dist` 中的 wheel。不需要发布公共包。Skills 的安装目录遵循所用 AI 助手规则；导出不会改助手配置。

## 独立演示

```powershell
uv sync --group dev
uv run python examples/create_demo.py --output D:/echo-demo
uv run echo-kit --workspace D:/echo-demo/qa --json workspace check
uv run echo-kit --workspace D:/echo-demo/qa lab compare arithmetic --variants baseline double --repeat 3
uv run echo-kit --workspace D:/echo-demo/qa verify run api
uv run echo-kit --workspace D:/echo-demo/qa verify run page
uv run echo-kit --workspace D:/echo-demo/qa services status
uv run echo-kit --workspace D:/echo-demo/qa runs list
```

示例生成互不嵌套的 frontend、backend、qa 三个目录，均不依赖魔方。`page` 需要正在运行的 Docker，首次启动容器需下载固定 Playwright 镜像及 npm 包。

## 配置与执行

共享 `echo-kit.toml` 与 `echo/` 适配、用例入 Git；`.echo-kit/local.toml` 深度覆盖共享配置，environment 覆盖随后生效。所有 `.echo-kit/` 状态不提交。绝对路径放本机覆盖，命令数组中的 `{python}` 指 Kit 解释器；业务 Python 应改为项目实际解释器。

命令必须使用数组，不隐式经过 Shell。`env_refs` 把指定环境变量传给适配器，敏感信息禁止写在 `command` 或 `env` 字面量。Kit 不记录全环境变量；项目脚本不得把秘密打印到 stdout 或写入证据。

全局选项置于模块前：`echo-kit --workspace PATH --environment NAME --json lab run CASE`。退出码 0 为已执行范围成功，1 为检查失败，2 为受阻或未验证，130 为中断。`services status` 的成功表示查询成功，请检查各实例的 ready 字段。

适配协议、认证和配置详见 [协议文档](docs/protocol.md)。业务依赖由项目管理，CLI 不导入业务模块。简单 `protocol="command"` 只核对退出码，不能证明语义正确。

## 运行与安全边界

托管服务持续运行；外部服务显式 `mode="external"`，不会停止。进程身份不匹配时不发送终止信号。运行存在 active.json 时阻止新用例与停服务，使用 `runs cleanup ID` 调用项目清理并核验终态，不能删除标记伪造收尾。

认证协议通过内存管道传秘密。只向受支持的系统凭据后端持久化；不可用时只保留当前调用内状态。独立 auth login 在这种情况下不会为下一条命令保留状态；verify 同次调用仍可使用。

报告只自动链接显式声明的产物。请求文件、适配输出、截图及 trace 可能包含业务数据，分享前审查。Kit 无法阻止恶意或错误适配器自行泄露数据；适配器是项目受信代码。

## 验证与限制

`uv run pytest` 运行核心测试；真实演示与 Docker 验收单独运行。具体证据见 `docs/validation.md`。macOS 未验证；不把单元模拟当成真实业务或平台验收。

第一版不提供 IDE 插件、Agent 引擎、通用验证码绕过、后台自动恢复。回放为已有记录与 Playwright trace 查看，不承诺重跑结果一致。

## 来源

服务归属、就绪等待与持续复用的设计，及 Lab 的案例／候选／独立重复执行概念，源自既有 Magic Cube Workbench 和 Agent Lab。为跨项目子进程协议重新实现，不引入原业务依赖、服务名、模型配置或私有凭据。
