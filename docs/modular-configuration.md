# 按模块拆分配置

0.12.0 支持根 `echo-kit.toml` 显式引用模块 TOML。小项目继续使用一个文件；模块独立维护有价值时再拆分，不会自动创建或搬迁业务配置。

```toml
# echo-kit.toml
schema_version = 1
name = "my-project"
includes = ["services/orders/echo.toml"]

[browser]
headless = false
```

```toml
# services/orders/echo.toml
[projects.orders]
path = "services/orders"

[cases.orders_refund]
project = "orders"
command = ["python", "scripts/check_refund.py"]
description = "验证订单退款"
category = "orders/refund"
services = ["orders_api"]
docs = "services/orders/docs/refund.md"

[services.orders_api]
project = "orders"
command = ["python", "server.py"]
```

模块文件可以放在工作区各目录，不限于 `echo/`。它们仍属于同一工作区，用例 ID 不会因文件位置产生前缀。

## 文件职责与覆盖

- 模块只接受 `projects`、`services`、`auth`、`cases` 及它们的 `environments.NAME` 覆盖。
- 工作区名称、schema_version、默认环境、output、browser、service_startup 和 documents 保留在根配置。
- includes 只能出现在共享根文件。模块、私有文件、environment 都不能再引用文件；不支持通配符或目录扫描。
- 同一类型的同名共享基础定义只能出现一次。同一 environment 内同一实体的共享覆盖也只能出现一次；重复时报告配置键及两个文件。环境覆盖可以补充基础定义，不同环境可以分别覆盖。
- 先组合共享根和模块，再合并 `.echo-kit/local.toml`，最后应用 environment；字典递归合并、数组整体替换。私有文件保持单个，不支持 includes。

例如，模块配置 `[environments.development.services.orders_api]` 已定义的端口，应在私有文件同名 environment 中覆盖；私有根级端口不会优先于共享 environment。修改端口还需核对项目脚本参数、就绪检查及访问地址，Kit 不会自动替换命令字符串。

## 路径与安全边界

移动定义不会改变路径含义：includes、projects 的目录和 cases 的 docs 均相对工作区根目录；命令在所选 project 的目录执行。上例脚本位于 `services/orders/scripts/check_refund.py`，不是相对 TOML 再拼一次 `services/orders/`。

引用必须是工作区内的 TOML 文件。缺失、解析失败、绝对路径、越界、重复文件、符号链接越界、嵌套仓库、子模块、私有材料及 Git 忽略文件会在业务执行前被拒绝。共享配置应纳入 Git；未跟踪、Git 不可用和非 Git 工作区在检查中返回未验证，不单凭这些情况阻止正常加载。

## 查找与调整

```sh
echo-kit --json workspace check
echo-kit --json cases list
echo-kit --json cases show orders_refund
```

`workspace check` 的 configuration 返回文件清单、共享／私有归属、Git 状态和问题，不输出整份配置。`cases show` 的 configuration_sources 按实际合并顺序列出基础、本机和 environment 来源，不包含配置值。先找用例再读相关 TOML；`docs map` 仍只负责 Markdown。

拆分已有文件时，在用户授权范围内保留 ID、依赖、命令和相对路径，移动一次定义、登记 include，确认最终生效配置与拆分前一致。不要借拆分复制秘密、合并工作区或重建服务。旧单文件项目、已有导出 Skill 不自动修改。

完整规则随包通过 `echo-kit guide echo-init --topic configuration` 获取；[公开协议](protocol.md#modular-configuration)与安装包同步发布。
