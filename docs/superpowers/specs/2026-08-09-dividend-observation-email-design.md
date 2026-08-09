# 红利观察独立邮件设计

## 目标

基于当前已经完成的 `930955 红利低波100` 观察图研究成果，新增一封独立的固定发送邮件。

这封邮件不是新的研究体系，而是把现有观察图生产化：

- 保留当前本地 `JSON -> HTML 预览` 的生成方式
- 新增一条 `邮件渲染与发送` 链路
- 邮件排版尽量贴近当前网页预览
- 邮件图表不依赖 ECharts 运行时，而改为预先渲染的静态 PNG

## 已确认约束

- 邮件是独立第 5 封，不并入 `市场估值日报`
- 发送频率为固定发送，不做条件触发
- 调度口径为每周一到周六发送，周日不发
- 即使节假日或无新数据，也照常发送
- 邮件主题日期使用发送日，而不是数据最新日
- 当天部分图表或区块失败时，邮件尽力发出，并在正文中明确标注失败
- SMTP 相关配置沿用现有 GitHub Secrets
- 新邮件收件人单独配置，使用 GitHub Variable
- 本地研究生成与网页预览方式必须保留，不因邮件化被替换

## 不做的事

- 不把这封邮件并回 `src/valuation`
- 不把邮件逻辑继续挂在 `src/research`
- 不新增择时信号、交易信号或投资建议
- 不做新的网页系统或前端交互
- 不要求邮件图与网页图像素级一致

## 总体方案

采用独立板块化方案，在仓库中新增 `src/dividend_observation/` 目录，职责与现有 `coal`、`commodity`、`rotation`、`valuation` 同级。

职责分层如下：

1. `src/research`
   继续负责研究数据与网页预览：
   - 生成 `data/research/dividend_observation_930955.json`
   - 生成 `preview/dividend_observation_930955.html`

2. `src/dividend_observation`
   负责邮件化生产输出：
   - 读取研究 JSON
   - 生成 4 张静态 PNG 图
   - 渲染邮件 HTML
   - 生成邮件预览 HTML
   - 正式发送邮件

这样可以保证：

- 研究逻辑与生产邮件逻辑解耦
- 后续窗口参数仍只改 `data/research/dividend_observation_config.json`
- 网页预览与邮件预览可以并行存在

## 目录设计

建议新增以下文件：

- `src/dividend_observation/__init__.py`
- `src/dividend_observation/data.py`
- `src/dividend_observation/charts.py`
- `src/dividend_observation/render.py`
- `src/dividend_observation/run.py`
- `tests/test_dividend_observation_email_charts.py`
- `tests/test_dividend_observation_email_render.py`
- `tests/test_dividend_observation_email_run.py`
- `preview/dividend_observation_email.html`
- `.github/workflows/dividend-observation.yml`

### 各文件职责

#### `src/dividend_observation/data.py`

负责两件事：

- 复用现有 `src.research.dividend_observation_chart` 生成或读取标准 JSON
- 提供邮件层统一的数据读取入口，避免 `run.py` 直接依赖 research 细节

原则：

- 研究 JSON 是唯一权威输入
- 邮件层不重新定义第二套指标口径

#### `src/dividend_observation/charts.py`

负责把邮件所需 4 个区块渲染成静态 PNG：

- 价格与回撤
- 利率相对吸引力
- 绝对定价
- 风格挤压

要求：

- 使用 Python 渲染，不依赖前端 JS
- 标题、配色、图例顺序尽量贴近现有网页
- 输出结果可用于两种模式：
  - 本地预览：转为 base64 data URI
  - 正式发信：转为 cid 内联图片

#### `src/dividend_observation/render.py`

负责邮件 HTML 渲染，整体结构尽量复用当前网页阅读顺序：

- 顶部 Hero
- 4 个摘要卡片
- 4 个图表 section

每个 section 包含：

- 标题
- 一句说明
- 一句公式说明
- 静态图

渲染层需要支持：

- `cid` 图片引用
- `base64` 图片引用
- 区块失败占位文案

#### `src/dividend_observation/run.py`

作为统一入口，支持两种模式：

- `python -m src.dividend_observation.run --preview`
  - 生成邮件预览 HTML，不发信
- `python -m src.dividend_observation.run`
  - 生成邮件内容并正式发送

编排职责：

- 读取或刷新研究 JSON
- 调用图表生成
- 收集区块成功/失败状态
- 生成主题
- 发送邮件

## 邮件内容结构

邮件内容尽量贴近当前网页预览，不重新设计第二套阅读结构。

### 顶部摘要区

保留 4 个摘要卡片：

- 最新日期
- 近窗回撤
- 绝对估值
- 当前状态

这里的“日期”显示研究数据里的最新数据日期，邮件主题仍显示发送日。

### 图表顺序

正文图表顺序固定为：

1. 价格与回撤
2. 利率相对吸引力
3. 绝对定价
4. 风格挤压

### 图片策略

本地预览：

- 使用 base64 data URI
- 产出单一 HTML 文件，直接浏览器打开

正式发信：

- 使用 `cid` 内联图片
- 由 `src/common/email.py` 的 `inline_images` 机制发送

原因：

- 邮件客户端对 `cid` 的兼容性通常优于超长 base64
- 邮件体积更可控
- 与仓库现有 `coal`、`rotation` 等模块模式一致

## 调度与日期规则

### Workflow 调度

新增独立 workflow：

- 文件：`.github/workflows/dividend-observation.yml`

调度规则：

- 每周一到周六运行
- 周日不运行

### 主题规则

主题日期按发送日，例如：

- `红利观察日报 | 2026-08-10`

不使用“数据更新至某日”作为主题主日期。

### 节假日与无新数据

无论是否有新数据：

- 都照常发信

这意味着：

- 节假日周内若数据未更新，仍发送
- 周六也发送，即使内容通常是周五收盘后的最新状态

## 配置设计

### 沿用的研究配置

继续使用：

- `data/research/dividend_observation_config.json`

该配置继续控制：

- `analysis_window_years`
- `display_window_years`

邮件层不单独维护第二套窗口配置。

### 新增收件人变量

新增 GitHub Variable：

- `DIVIDEND_OBSERVATION_RECEIVER_EMAIL`

用途：

- 只供这封新邮件使用
- 不影响现有 `RECEIVER_EMAIL`

### SMTP 配置

正式发信沿用现有 SMTP Secrets，不新增重复 Secret。

## 失败策略

采用“尽力发出”的策略。

### 可容忍失败

以下失败不应阻断整封邮件：

- 某一张图生成失败
- 某一区块数据缺失
- 某个摘要指标为空

呈现方式：

- 对应卡片显示 `-`
- 对应图表 section 显示“该图生成失败”或“该区块暂无数据”
- 日志明确打印失败项

### 不可容忍失败

以下失败可以中止整封邮件并触发告警：

- 基础研究 JSON 无法生成且无法读取已有本地 JSON
- 邮件 HTML 无法构建
- SMTP 发送失败

### 与告警配合

工作流和运行日志应能明确区分：

- 部分区块失败，但邮件已发出
- 整体失败，邮件未发出

这样后续 webhook 通知不会只剩模糊的“失败”。

## 本地运行方式

保留并明确三套命令：

研究数据生成：

```powershell
python -m src.research.dividend_observation_chart
```

网页预览：

```powershell
python -m src.research.dividend_observation_chart_preview
```

邮件预览：

```powershell
python -m src.dividend_observation.run --preview
```

正式发信：

```powershell
python -m src.dividend_observation.run
```

## 测试要求

至少补齐以下测试：

### 图表层

- 4 张图在最小样本下可生成 PNG
- 某个指标全空时，图表层返回失败结果而不是抛出不可控异常

### 渲染层

- 预览模式会输出 base64 图片引用
- 正式邮件模式会输出 `cid:` 图片引用
- 区块失败时会渲染占位文案
- 卡片缺值时显示 `-`

### 编排层

- `--preview` 只生成 HTML，不发送邮件
- 正式模式会调用 `send_email`
- 收件人优先读取 `DIVIDEND_OBSERVATION_RECEIVER_EMAIL`
- 部分图失败时仍发送
- 研究 JSON 缺失且无法生成时中止

### Workflow 层

- workflow 使用新 Variable
- workflow 入口命令正确
- workflow 调度为周一到周六

## 文件关系

最终关系应为：

- `src/research/dividend_observation_chart.py`
  负责研究数据
- `src/research/dividend_observation_chart_preview.py`
  负责网页预览
- `src/dividend_observation/*`
  负责邮件生产

这条边界必须保持清晰，不要把邮件发信逻辑重新塞回 `src/research`。

## 风险与取舍

### 1. 邮件图与网页图无法完全一致

这是接受的取舍。

原因：

- 网页端依赖 ECharts
- 邮件端不能运行 JS

因此目标是“信息结构一致、视觉风格接近”，而不是像素级还原。

### 2. 固定发送会产生“内容未变也发”

这是需求本身接受的行为，不视为 bug。

### 3. 部分失败但继续发送，可能让正文存在缺口

这是有意选择，用来换取稳定性和连续性。

前提是：

- 缺口必须明确标注
- 告警必须能定位具体失败区块

## 后续实现顺序

建议实现顺序：

1. 先补邮件渲染测试
2. 再补图表生成测试
3. 建立 `src/dividend_observation/` 基础结构
4. 打通 `--preview` 生成邮件预览
5. 接入正式发信
6. 最后接 workflow

这样可以先本地闭环，再上 CI 调度。
