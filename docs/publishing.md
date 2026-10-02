# 发布与随包文档更新

仓库：`shinnjyuu/echo-kit`。包名：`shinnjyuu-echo-kit`。本机制约束 Skill、公开协议、CLI 文档入口与安装包的同步交付。

## 哪些修改需要发版

**随包分发的 Skill 和协议属于产品内容。修改这些内容后，需要通过提升版本并发布新包交付，即使 Python 执行逻辑没有变化。** 可以将同一轮相关修改合并到一个版本发布。

| 修改范围 | 需要同步的内容 | 交付方式 |
| --- | --- | --- |
| Skill 正文或随包参考资料 | `src/echo_kit/skills/` 中的实际资源、相关用法和 CLI 文档获取结果 | 新版本安装包 |
| 新增、重命名 Skill，或改变关联命令 | Skill 资源、受影响的 CLI 索引／读取／导出逻辑、命令实现与帮助、示例 | 新版本安装包 |
| 公开协议 | `docs/protocol.md` 与 `src/echo_kit/skills/references/protocol.md`；受影响的命令、适配规则和检查 | 新版本安装包 |
| 仅供仓库阅读的介绍、维护或验证记录 | 相关链接与使用说明 | Git 推送即可更新仓库；若需更新 PyPI 展示的 README，随新包发布 |

修改 Skill 正文时，CLI 文档入口应直接读取更新后的包资源，无需在 CLI 代码里再保存一份正文。涉及名称、参数、行为或帮助变化时，才同步修改对应实现。文档中出现的命令必须与实际支持范围一致。

## CLI 获取文档的约定

CLI 提供的文档必须来自**当前运行版本**的包资源，与该版本的执行行为匹配；不得在运行旧版工具时静默拉取 GitHub `main` 的最新说明。

当前已经实现的入口是 `echo-kit skills export EMPTY_DIR`。它将三个 Skill 及协议副本导出到不存在或为空的目录，再由 AI 读取文件；无需先初始化工作区。仓库根目录的 `README.ai.md` 和验证文档不属于这项导出内容。

计划补充以下只读入口，**尚未实现，不能作为当前可用命令交付**：

```text
echo-kit skills list
echo-kit skills show NAME
echo-kit protocol show
```

实现这些入口时，和 `skills export` 共用包内资源；无需初始化工作区或联网即可读取，并让调用者能识别文档所属的工具版本。增加命令时同步更新 `--help`、AI 接入指南和相关验证，将这里的实现状态一并更新。

## 从修改到安装包的验证

1. 更新实际 Skill 资源；修改公开协议时，确认仓库正文与随包副本完全一致。同步受影响的 CLI 行为、文档入口、帮助和示例。
2. 运行相关核心检查和 Skill 校验。新增 CLI 文档入口时验证实际输出与包资源一致，导出仍保留参考资料的相对路径，已有项目文件不会被覆盖。
3. 为本轮发布选择新版本，同步修改 `pyproject.toml` 与 `src/echo_kit/__init__.py`，运行 `uv lock`、`uv run pytest`、`uv build --no-sources`。
4. 在源码目录之外的独立环境安装本轮构建的 wheel，检查版本、帮助及实际支持的文档入口。将导出的 Skill 和协议与本轮预期内容比较，确认更新确实进入安装包。仅从源码执行成功不足以证明安装包交付正确。
5. 按本轮已有授权提交、推送并发布。若本轮范围仅包括修改文档或普通推送，交付时明确标记“随包文档待发版”，不要声称已安装用户已经获得更新。
6. 发布后，从仓库外使用新发布的具体版本复验版本号和文档内容。确认包可获取且包含本轮修改后，才报告随包文档已发布。

首次查询可用版本可用 `uvx --from shinnjyuu-echo-kit@latest echo-kit --version`；一轮接入或验收随后固定版本。下面的 `NEW_VERSION` 和 `EMPTY_DIR` 是占位符，需替换为本次实际发布版本和空目录：

```sh
uvx --from shinnjyuu-echo-kit@NEW_VERSION echo-kit --version
uvx --from shinnjyuu-echo-kit@NEW_VERSION echo-kit --help
uvx --from shinnjyuu-echo-kit@NEW_VERSION echo-kit skills export EMPTY_DIR
```

交付时分别说明仓库提交、已发布包版本、已验证的文档入口，以及项目导出副本是否完成更新。发布验证和用户升级是不同步骤。

## 已安装工具与项目副本怎样更新

GitHub 推送不会自动更新已经安装或缓存的工具。用户需要升级安装包，或将固定的 `uvx` 调用切换到包含更新的已发布版本，才能取得该版本内置的 Skills 和协议。

已经导出到业务项目的 Skills 是独立副本。升级安装包不会覆盖它们，也不会修改项目适配脚本和配置。需要更新时，先用选定版本导出到新的空目录，比较差异，保留团队修改并合并所需内容，保持 Skill 与 `references/protocol.md` 的相对路径；在项目使用入口记录采用的工具版本。

## PyPI 发布配置与触发方式

普通 `main` 推送仅运行 Windows/Linux 核心测试。推送 `v*` 标签时，通过测试后构建并使用 OIDC 发布；也可对已有标签手动重跑工作流。只有提交或推送到 `main`，随包文档仍属于待发版内容。

准备好发布版本并完成验证后，创建与包版本一致的标签，例如包版本为 `0.1.2` 时使用 `v0.1.2`，再推送该标签。GitHub Release 可随后附加版本说明，不是此工作流的触发条件。已发布文件不能覆盖；后续修改需使用新版本。

首次配置 Trusted Publishing 时，在 [PyPI 发布设置](https://pypi.org/manage/account/publishing/)添加 pending publisher：

| 字段 | 值 |
| --- | --- |
| PyPI Project Name | shinnjyuu-echo-kit |
| Owner | shinnjyuu |
| Repository name | echo-kit |
| Workflow name | publish.yml |
| Environment name | pypi |

启用账号双因素认证。首次上传成功才实际占用包名。无需保存长期 API Token。未完成 PyPI 授权时不要推送发布标签。
