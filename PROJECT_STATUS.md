# Project Status

- 项目：校园多源文档 RAG 学业规划助手（启明大学模拟资料）
- 当前运行模式：**默认 CPU**（不申请 GPU / CUDA；`gpu` Profile 保持关闭）
- 当前阶段：**阶段 0 已完成**
- 下一阶段：**阶段 1 — Docker 前后端骨架**
- 最近更新：2026-09-26

## 阶段状态

| 阶段 | 名称 | 状态 |
|---|---|---|
| 0 | 环境预检 | completed |
| 1 | Docker 前后端骨架 | not_started |
| 2 | 模拟语料、上传与解析 | not_started |
| 3 | 语义切片与 Chroma | not_started |
| 4 | FTS5 与混合检索 | not_started |
| 5 | Reranker | not_started |
| 6 | SSE 问答与引用 | not_started |
| 7 | 确定性学分规则引擎 | not_started |
| 8 | Vue 核心页面 | not_started |
| 9 | RAG 评测与安全测试 | not_started |
| 10 | 一键启动与复现 | not_started |

## 阶段 0 结论

- 环境闸门通过（Overall status: PASS），详见 `ENVIRONMENT_REPORT.md`。
- Docker daemon 可连接（ServerVersion 29.5.3）；Compose v5.1.4；context `desktop-linux`。
- 端口 5173、8000 可用；磁盘空间满足（各盘可用 > 200 GB）。
- Git 仓库已初始化（分支 `main`），本仓库 identity 已配置，文档基线提交已完成。
- 保留 WARNING（不阻塞）：宿主机 Node/pnpm/uv 缺失（统一在容器内执行）、可选 GPU 容器验证未执行。

## 备注

- 本文件仅用于阶段状态跟踪；业务实现、构建与测试均在 Docker 容器内执行。
- 进入阶段 1 前须按 `docs/IMPLEMENTATION_PLAN.md` 的阶段 1 验收标准执行。
