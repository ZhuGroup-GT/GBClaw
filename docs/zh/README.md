<p align="center">
  <picture>
    <source media="(prefers-color-scheme: dark)" srcset="../assets/GBClaw-logo-light.svg">
    <source media="(prefers-color-scheme: light)" srcset="../assets/GBClaw-logo.svg">
    <img src="../assets/GBClaw-logo.svg" width="360" alt="GBClaw — 多智能体晶界研究框架">
  </picture>
</p>

# GBClaw 文档

[项目总览](../../README.md) · [English documentation](../README.md)

GBClaw 通过专职 agent、科学工具和持久项目记录来组织晶界原子模拟研究。这组文档说明如何使用系统、为什么这样划分职责，以及扩展功能时应该从哪里入手。

## 按目标选择阅读顺序

| 你的目标 | 从这里开始 | 继续阅读 |
| --- | --- | --- |
| 完成第一次研究任务 | [快速上手](getting-started.md) | [科学工具](tools.md)和[交互选胞](dichromatic-map.md)。 |
| 理解架构设计 | [架构与设计逻辑](architecture.md) | [Agent 运行时](agent-runtime.md)和[项目记忆](project-memory.md)。 |
| 配置模型和计算资源 | [配置](configuration.md) | [Coding 环境](coding-agent.md)和 [API 与界面](api-and-ui.md)。 |
| 新增 agent 或科学工具 | [扩展开发指南](extension-guide.md) | [源码地图](src-code-map.md)和对应的[工具工作流](tools.md)。 |
| 找回或检查以前的结果 | [项目记忆](project-memory.md) | [项目文件与 API](api-and-ui.md)。 |

## 文档索引

| 文档 | 回答的问题 | English |
| --- | --- | --- |
| [快速上手](getting-started.md) | 需要安装什么，怎样配置和启动？ | [Getting started](../getting-started.md) |
| [架构与设计逻辑](architecture.md) | 为什么需要专职 agent、分层记忆和研究者审阅？ | [Architecture and design](../architecture.md) |
| [Agent 运行时](agent-runtime.md) | 一条请求怎样变成委派任务和最终回答？ | [Agent runtime](../agent-runtime.md) |
| [科学工具](tools.md) | 应该选哪个工作流，需要什么输入，会产生什么输出？ | [Scientific tools](../tools.md) |
| [项目记忆](project-memory.md) | 保存了什么，哪些进入模型上下文，如何找回证据？ | [Project memory](../project-memory.md) |
| [扩展开发指南](extension-guide.md) | 怎样新增 agent、工具、orchestrator 工具、路由或模型 provider，并检查它们？ | [Extension guide](../extension-guide.md) |
| [配置](configuration.md) | 模型设置、环境名称和资源预算分别放在哪里？ | [Configuration](../configuration.md) |
| [Coding agent](coding-agent.md) | 自定义脚本怎样选择环境并发布结果？ | [Coding agent](../coding-agent.md) |
| [双色点阵选胞](dichromatic-map.md) | 交互图中的选择怎样变成可计算的晶胞？ | [Dichromatic map](../dichromatic-map.md) |
| [API 与界面](api-and-ui.md) | 客户端、执行占用、流式输出和项目文件如何配合？ | [API and UI](../api-and-ui.md) |
| [源码地图](src-code-map.md) | 需要修改的代码属于哪个模块？ | [Source map](../src-code-map.md) |
| [后续设计](roadmap.md) | 哪些扩展方向和设计约束值得继续探索？ | [Roadmap](../roadmap.md) |

[框架论文和相关科学研究](../references.md)介绍研究背景。[图片来源](../assets/README.md)说明文档插图出自哪里。

## 阅读与维护约定

先阅读用途和设计逻辑，再根据需要查询工具名、API 字段和默认值等细节。实现说明以当前仓库为准；论文解释科学动机和总体设计，概念图中的部分集成可能还需要可选软件或扩展。

中英文文档应描述相同的系统行为。普通 Markdown 段落在源码中保持一段一行，由 GitHub 或编辑器自动折行；代码块、表格、列表和有意保留的换行仍使用各自的结构。仓库文件使用相对链接，总览通过链接引用详细契约，减少重复维护。

