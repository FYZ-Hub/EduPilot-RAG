# 校园多源文档 RAG 项目：Trae 阅读入口

本文件是项目入口索引，不是始终注入上下文的完整规则。不要再把整份产品规格复制进 Trae Rules。

## 已拆分的规范

| 文件 | 唯一职责 | 何时读取 |
|---|---|---|
| `.trae/rules/project-guardrails.md` | 常驻行为、安全、阶段纪律和汇报规则 | 每轮自动生效 |
| `docs/PRODUCT_SPEC.md` | 产品、架构、运行模式、数据模型、API、RAG 和页面契约 | 开始业务实现或修改公共接口前 |
| `docs/UI_SPEC.md` | 设计系统、页面线框、组件状态、响应式和前端验收 | 创建前端骨架或实现任一页面前 |
| `docs/DEMO_DATA_SPEC.md` | 模拟语料、生成器、异步初始化、状态机、幂等与恢复 | 涉及演示资料、文档入库或任务进度时 |
| `docs/IMPLEMENTATION_PLAN.md` | 环境预检、阶段 0–10、测试、验收和完成定义 | 选择或验收当前阶段时 |

## 首次交给 Trae

1. 用项目根目录打开 Trae，确认 `.trae/rules/project-guardrails.md` 已被识别为项目规则。
2. 首次提示使用：

   > 请先读取 `.trae/rules/project-guardrails.md` 和 `docs/IMPLEMENTATION_PLAN.md`，本轮只执行阶段 0 环境预检，生成 `ENVIRONMENT_REPORT.md`，不要创建业务代码。

3. 环境闸门通过后，每轮明确指定一个阶段；Trae 再按路由读取 Product Spec 或 Demo Data Spec。

## 规范优先级

发生表述冲突时按以下唯一归属判断：

1. 安全、隐私和工作协议：以 `project-guardrails.md` 为准。
2. 产品、架构、公共类型和普通 API：以 `PRODUCT_SPEC.md` 为准。
3. 页面视觉、布局、组件和交互呈现：以 `UI_SPEC.md` 为准。
4. 模拟资料、演示 API、异步任务和恢复语义：以 `DEMO_DATA_SPEC.md` 为准。
5. 阶段顺序、验收命令和完成标准：以 `IMPLEMENTATION_PLAN.md` 为准。

不要把一个文件中的摘要解释为对其唯一事实来源的覆盖。

## 本次优先修复

- 默认模式固定使用 CPU；CUDA 仅通过 `docker-compose.gpu.yml` 与 `gpu` Profile 显式启用。
- 演示导入不再只按 SHA-256 判断跳过，而是依据文件校验值、流水线指纹和持久化阶段检查点续跑。
- `POST /api/demo/seed` 改为返回 HTTP 202 的持久化异步任务，通过 `/api/demo/jobs/{job_id}` 查询；容器重启后可恢复。
