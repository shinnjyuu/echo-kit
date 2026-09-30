# PyPI 发布

仓库：`shinnjyuu/echo-kit`。包名：`echo-kit`。

首次在 https://pypi.org/manage/account/publishing/ 添加 pending publisher：

| 字段 | 值 |
| --- | --- |
| PyPI Project Name | echo-kit |
| Owner | shinnjyuu |
| Repository name | echo-kit |
| Workflow name | publish.yml |
| Environment name | pypi |

启用账号双因素认证。首次上传成功才实际占用包名。无需保存长期 API Token。

普通 main 推送仅运行 Windows/Linux 核心测试。推送 `v*` 标签时，通过测试后构建并使用 OIDC 发布；也可对已有标签手动重跑工作流。

发布前同步修改 pyproject.toml 和 src/echo_kit/__init__.py 的版本，运行 `uv lock`、`uv run pytest`、`uv build --no-sources`。
提交并推送后，创建与包版本一致的标签，例如 `v0.1.0`，并推送该标签。GitHub Release 可随后附加版本说明，不是此工作流的触发条件。

发布后从仓库外验证 `uvx echo-kit@latest --version` 和 `uvx echo-kit@0.1.0 --help`。
一轮验收开始时获取最新版，随后固定本轮版本。包更新不覆盖项目适配脚本、配置或已导出的 Skill 副本。

已发布文件不能覆盖；修改内容需提升版本。未完成 PyPI 授权时不要推送发布标签。
