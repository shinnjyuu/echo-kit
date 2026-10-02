# 独立演示

本示例用于体验 Echo Kit 的实验、API 验收和页面验收。回到[产品介绍](../README.md)；为自己的项目接入请读 [AI 接入指南](../README.ai.md)。

## 准备示例

示例生成互不嵌套的 `frontend`、`backend` 和 `qa` 三个目录。准备 Python 3.11+ 和 uv，在 Echo Kit 源码目录执行以下命令。示例使用 Windows 路径，其他系统请替换成自己的绝对路径；输出目录必须不存在或为空。

```sh
uv sync --group dev
uv run python examples/create_demo.py --output D:/echo-demo
uv run echo-kit --workspace D:/echo-demo/qa --json workspace check
```

## 对比两个实验方案

这个实验计算 `[2, 3]` 的和，并对比原值与乘以 2 的结果，各运行三次。它不需要服务、登录或 Docker。

```sh
uv run echo-kit --json operations list
uv run echo-kit --workspace D:/echo-demo/qa --actor "示例实验" --json lab compare arithmetic --variants baseline double --repeat 3
```

预期得到六条独立运行记录，`answer` 指标分别为 5 和 10；每条记录都应有通过的 `sum` 检查。

## 验收本地 API

以下步骤会启动示例自己的本地 API 服务，按示例认证流程登录，下载文件并检查内容。示例默认使用端口 18761 和 18762；如有冲突，创建示例时可通过 `--api-port` 和 `--web-port` 指定空闲端口，不要停止未知实例。

```sh
uv run echo-kit --json operations list
uv run echo-kit --workspace D:/echo-demo/qa --json services status
uv run echo-kit --workspace D:/echo-demo/qa --actor "示例 API 验收" --json verify run api
```

示例使用公开测试凭据，验证的是示例认证和下载流程。

## 可选：验收页面

需要正在运行的 Docker。首次启动会下载固定版本的 Playwright 镜像及 npm 包；浏览器服务器和 Python 客户端均使用 Playwright 1.63.0。用例会准备示例前后端，打开页面、下载文件并保存截图和 trace。

```sh
uv run echo-kit --json operations list
uv run echo-kit --workspace D:/echo-demo/qa --json services status
uv run echo-kit --workspace D:/echo-demo/qa --actor "示例页面验收" --json verify run page
```

## 查看结果与结束演示

```sh
uv run echo-kit --workspace D:/echo-demo/qa --json runs list
uv run echo-kit --workspace D:/echo-demo/qa --json services status
```

运行记录和 HTML 报告默认位于 `D:/echo-demo/qa/.echo-kit/runs/`。页面用例通过时还会留下截图、下载文件和 Playwright trace。

服务在验收结束后保持运行。确认本次演示的运行已完成，且没有其他会话占用后，可停止演示托管的服务；只有运行过页面验收并启动了托管浏览器时才执行 `browser down`。

```sh
uv run echo-kit --json operations list
uv run echo-kit --workspace D:/echo-demo/qa --actor "结束示例" services down web api
uv run echo-kit --workspace D:/echo-demo/qa --actor "结束示例" browser down
```

发生未确认完成的运行时，先按 [AI 接入指南](../README.ai.md)核查和清理，保留原始失败记录。真实演示、核心测试与平台验证的范围见[验证记录](validation.md)。
