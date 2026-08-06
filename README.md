# market-daily

A 股市场每日简报:扫描 4 个板块,每个板块生成一封 SMTP 邮件日报。整合自 `jisilu_ggx` 与 `monitor_drawdown` 两个历史仓库。

## 4 个板块

| 板块邮件 | 内容 |
|---------|------|
| 市场估值 | 估值分位(PE/PB/股息率/股债收益差)+ 高股息(集思录主表 + 东财补充池 + 巨潮 TTM)+ 果仁行业估值 + 风格轮动 + 汇率图 |
| 资产轮动 | ETF 20 日动量轮动 |
| 转债行情 | 低价债筛选(三低排序)+ 三低轮动净值 + 转债指数图 + 董秘互动 + 日历下修提醒 |
| 商品极值 | CCTDA 煤炭日报图片转发 |

## 目录结构

```
src/
  common/      # 公共基础设施:env / jisilu(账密登录) / email(SMTP) / alerts(报警+重试) / storage(数据备份) / fonts
  valuation/   # 市场估值板块
  rotation/    # 资产轮动板块
  convertible/ # 转债行情板块
  commodity/   # 商品极值板块
  preview/     # 统一 preview 生成 + 数据校验
data/          # 持久化数据(state/archive/cninfo/cb_bonds/dividend_universe/whitelist/cb_index_history.json),靠 git commit 持久化
config/        # env.example + 各板块 yaml
.github/       # workflow + composite action
```

## 运行

```powershell
pip install -r requirements.txt
Copy-Item .\config\env.example .\.env.local   # 填入凭据

# 单板块运行(发邮件)
python -m src.valuation.run
python -m src.rotation.run
python -m src.convertible.run
python -m src.commodity.run

# 本地预览(不发信,生成 preview/*.html)
python -m src.preview.generate valuation

# 数据校验
python -m src.preview.verify
```

## 设计要点

- **集思录统一账密登录**(`src/common/jisilu.py`),不再使用 Cookie。
- **webhook 只做异常报警**,4 封日报纯 SMTP。
- **数据持久化靠 git commit**:板块运行后把 `data/state/*.json`、`data/archive/` 变更回提交(CI 自动),跨运行保持状态。
- **板块邮件 = 多 section 聚合**:任一 section 有新数据即发整封;节假日全板块无新数据则静默退出。

## CI

GitHub Actions:4 个板块 workflow(工作日收盘 UTC 07:30 触发)+ 数据归档/巨潮预热 workflow。详见 `.github/workflows/`。
