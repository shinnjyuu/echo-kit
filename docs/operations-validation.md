# 共享操作记录改造验收

日期：2026-09-30。仅 Echo Kit。验证完成后按用户要求纳入 0.1.1 发布。

## 已验证

- Windows / Python 3.12：41 passed，1 skipped（POSIX 信号测试）。
- Linux / Ubuntu Noble 容器 / Python 3.12：42 passed，包括 POSIX 中断路径。
- 三个 Skill 的结构校验通过。
- 两个实际 CLI 同时争抢同一资源，只有一个取得使用权；另一个返回 resource_busy，不执行验收。
- 不同工作区指向同一目标时冲突；不同资源并行；状态查询及独立登录不受服务验收阻挡。
- 两个独立 CLI 并发启动不同 HTTP 服务，状态文件保留两个实例，随后仅停止测试自有实例。
- 未确认终态只阻挡关联资源；清理确认后释放；原验收结论仍为 unverified。
- PID 创建时间不匹配按失联处理，不自动释放；仍存活的执行子进程阻止人工解除。
- 构建期间源码变化阻止启动；固定产物启动后继续修改源码，不覆盖启动快照及哈希。

## 可复跑的本机 HTTP 演示

`uv run python examples/validate_concurrency.py --output <不存在的输出目录>`

创建两个独立工作区，使用工具托管的真实本机 HTTP 服务。A 验收期间，B 验收同一服务和重启均被阻止；只读查询和独立 Lab 正常；A 结束后 B 成功。结束时停止该演示启动的服务。没有连接业务环境或重发业务请求。

本轮 Windows 证据位于 `D:/Codex/echo-kit-concurrency-20260930/results.json`，各工作区 `.echo-kit/runs/` 有 JSON 与 HTML 报告，用户目录 `user-data/operations/` 有操作记录。
Linux 容器演示位于同目录的 `linux-demo-final/`。`windows-tests.xml`、`linux-tests.xml` 保存平台测试结果。

## 验证边界

- 浏览器生命周期与共享占用使用针对性受控测试；本轮没有重新执行真实 Docker 页面登录和下载验收。
- 登录独立性与锁行为使用受控认证适配器，没有连接真实企业登录服务。
- Linux 结果来自容器，不代表所有桌面系统；macOS 未验证。
- 仅同机同系统用户、同一协调目录有效；DNS 别名需显式共享 resource_id；旧版命令和外部手工操作不受保护。
- 源码直接运行、热更新及第三方构建正确性不由占用机制保证；用户应声明实际使用的产物。构建指纹要求 Git 项目且生成物正确忽略。
- 不能推断远端业务任务已经结束；需清理适配器确认，或用户明确核对后人工解除。不会按超时自动抢占。
- 测试创建了独立本机验证镜像 `echo-kit-operations-test:local`；未改动已有业务容器。
