# 红利观察估算账本设计

## 目标

先为 930955 产出并保存可追溯的 T 日基础估算数据，暂不改变现有研究页、邮件和正式估值历史。

## 范围

- 新建独立 JSON 账本，不修改 `index_valuation_percentile`、`index_dividend_ratio` 或现有研究 payload。
- 当指数收盘价日期晚于正式 PE/PB/股息率日期时，为缺少正式值的交易日创建估算记录。
- PE、PB、股息率使用最近一日正式值和指数价格比例估算。
- 10Y 国债使用当天可获取的真实值；两条利差据此计算。
- 同一估算日期重复执行时按日期更新，确保可以安全重跑。
- 本阶段不写入正式值、不计算误差、不接入页面或邮件。

## 数据规则

设基准正式估值日为 `base_date`，其指数收盘为 `base_close`；目标日为 `estimate_date`，收盘为 `estimate_close`：

`price_factor = estimate_close / base_close`

- `pe_ttm = base_pe_ttm * price_factor`
- `pb_lf = base_pb_lf * price_factor`
- `dividend_yield = base_dividend_yield / price_factor`
- `dividend_yield_spread = dividend_yield - bond_10y`
- `earnings_yield_spread = 100 / pe_ttm - bond_10y`

账本记录估算日期、基准正式值日期、收盘价、价格因子、当天国债日期和值，以及上述五项估算结果。所有记录带 `status: "estimated"`，避免与未来正式值混淆。

## 存储与运行

- 新文件：`data/research/dividend_observation_930955_estimates.json`。
- 生成器读取既有归档中的指数收盘、正式估值和股息率；国债从当前可用的实时数据取得，并记录实际数据日期。
- 生成器提供独立命令入口，写出账本并返回本次新增或更新的记录数。
- 正式估值到达后的比对、`official_value`、绝对误差、相对误差和前端消费不在本阶段实现。

## 验收条件

- 以 2026-08-07 正式估值和 2026-08-10 收盘价为输入，可产生 2026-08-10 的一条估算记录。
- 记录包含 PE、PB、股息率、两条利差、完整基准日期和国债来源日期。
- 已有正式估值的日期不产生估算记录。
- 重复运行不会产生相同日期的重复记录。
- 现有 `dividend_observation_930955.json` 与两个预览文件不因该生成器而改变。
