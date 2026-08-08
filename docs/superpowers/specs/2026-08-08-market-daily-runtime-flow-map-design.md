# market-daily 运行地图设计

## 目标

把当前仓库的运行方式整理成一份能直接拿来看的地图，回答四个问题：

- 哪个 workflow 负责什么
- 哪些流程会发邮件，哪些只是预热/归档
- 哪些数据会落盘，哪些会被 `git commit`
- 哪些条件会让流程静默跳过

## 范围

只覆盖当前 `market-daily` 仓库的真实运行链路，不再展开旧仓库实现细节。

## 总体结构

当前仓库不是“一个脚本跑到底”，而是三层：

1. **调度层**
   `.github/workflows/*.yml` 决定什么时候跑、传什么变量、跑完是否提交文件。
2. **板块编排层**
   `src/valuation`、`src/rotation`、`src/convertible`、`src/coal`、`src/commodity` 负责抓数、组装正文、判断是否发信。
3. **数据持久化层**
   `data/state`、`data/archive`、`data/cninfo`、`data/cb_index_history.json` 通过 workflow 的 `git add/commit/push` 持久化。

## 五个板块

### 1. 市场估值

入口：`valuation.yml` -> `python -m src.valuation.run`

流程：

- 先跑估值核心，再拼接风格轮动、汇率图、高股息、果仁行业估值。
- `Guorn` 区块会先取数、归档，再渲染进邮件。
- 以估值核心的 `index_valuation_date` 作为整封邮件是否需要发送的守卫。

数据：

- 会写 `data/state/valuation.json`
- 会写 `data/archive/`
- 会写 `data/cninfo/`

提交：

- workflow 末尾提交 `data/state data/archive data/cninfo`

### 2. 资产轮动

入口：`rotation.yml` -> `python -m src.rotation.run`

流程：

- 先跑轮动策略
- 再生成净值图
- `last_run_date` 不变时静默跳过

数据：

- 会写 `data/state`
- workflow 的提交目标里包含 `data/archive`，但实际主要是 `state`

提交：

- workflow 末尾提交 `data/state data/archive`

### 3. 转债行情

入口：`convertible.yml` -> `python -m src.convertible.run`

流程：

- 主 section 是筛选结果
- 辅 section 是三低轮动、董秘互动、日历提醒、指数图
- 主 section 失败则整板中止
- 辅 section 失败只报警，不影响整封发出

数据：

- 会写 `data/state`
- 会写 `data/archive`

提交：

- workflow 末尾提交 `data/state data/archive`

### 4. 煤炭日报

入口：`coal.yml` -> `python -m src.coal.run`

流程：

- 先抓最新 CCTDA 日报
- 按 `article_url` 去重
- 已发送过则跳过

数据：

- 会写 `data/state/cctda_coal_daily.json`
- workflow 的提交目标里包含 `data/archive`，但实际主要是 `state`

提交：

- workflow 末尾提交 `data/state data/archive`

### 5. 商品极值

入口：`commodity.yml` -> `python -m src.commodity.run`

流程：

- 先全量扫描品种
- 如果配置了 `skip_if_no_today_data`，且当天没有任何今日数据，则跳过发信
- 这是为了避免长假期间发“旧日报”

数据：

- 目前不写回 git 持久化数据

提交：

- 无

## 两个后台 workflow

### 1. 归档刷新

入口：`refresh_archive.yml` -> `python -m src.valuation.refresh_archive`

职责：

- 提前刷新估值相关归档
- 包含指数、10Y 国债、汇率、转债等权指数
- 这是给后面的板块邮件做回退和补底，不是独立邮件

数据：

- 会写 `data/archive/`
- 会写 `data/cb_index_history.json`

提交：

- workflow 末尾提交归档文件

### 2. 巨潮财报缓存

入口：`cninfo_backup.yml` -> `python -m src.valuation.dividend.cninfo_backup`

职责：

- 夜间分片预热
- 白天失败重试
- 给市场估值里的高股息/财报依赖提供缓存

数据：

- 会写 `data/cninfo`
- 会写 `data/dividend_universe`

提交：

- workflow 末尾提交缓存文件

## 你最该记住的边界

- **邮件流程负责“用数据”**
- **workflow 负责“提交数据”**
- **后台 workflow 负责“提前补数据”**
- **不是所有板块都会 commit**
- **不是所有跳过都是 bug**

## 旧仓库

`jisilu_ggx`、`monitor_drawdown`、`commodity-monitor-days` 的定时 workflow 已停，只保留手动触发，用来观察 3 到 5 个交易日是否有漏报或漏归档。
