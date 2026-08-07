# 部署指南

market-daily 在 GitHub Actions 上跑 8 个 workflow。CI 凭据来自 **GitHub Secrets**,非敏感配置来自 **GitHub Variables**;本地开发则用 `.env.local`(见 `config/env.example`)。

## 1. GitHub Secrets

在仓库 **Settings → Secrets and variables → Actions → New repository secret** 逐个添加:

| Secret | 说明 | 用于 workflow |
|--------|------|--------------|
| `JISILU_USERNAME` | 集思录账号(统一账密登录) | valuation / rotation / convertible / cninfo_backup / preview(generate) |
| `JISILU_PASSWORD` | 集思录密码 | valuation / rotation / convertible / cninfo_backup / preview(generate) |
| `SMTP_USER` | 发信邮箱(QQ 邮箱) | valuation / rotation / convertible / coal / commodity |
| `SMTP_PASS` | 邮箱授权码(**非**登录密码) | valuation / rotation / convertible / coal / commodity |
| `RECEIVER_EMAIL` | 收件人,逗号分隔多个 | valuation / rotation / convertible / coal / commodity |
| `ALERT_WEBHOOK` | 异常报警 webhook(企业微信/钉钉/飞书) | 全部 workflow |
| `GUORN_COOKIE` | 果仁行业估值 cookie | valuation / preview(generate) |
| `EASTMONEY_XUANGU_COOKIE` | 东财选股补充池 cookie(可选) | valuation / cninfo_backup / preview(generate) |
| `DIVIDEND_EMAIL_SUPPLEMENT_XCID` | 东财补充池 xcid | valuation / cninfo_backup |
| `TTM_PARENT_NET_PROFIT_MIN_YI` | 高股息 TTM 归母净利润阈值(亿),默认 10(非敏感,放 secrets 便于统一管理) | valuation |
| `DIVIDEND_SUPPLEMENT_PE_TTM_MAX` | 东财补充池 PE-TTM 上限,默认 15 | valuation |

> `EMAIL_FROM` / `EMAIL_SMTP_HOST` / `EMAIL_SMTP_PORT` 有代码默认值(QQ 465),无需设;要改再加。

## 2. GitHub Variables

在 **Settings → Secrets and variables → Actions → Variables** 添加(均有代码默认值,可省略):

| Variable | 默认 | 用于 workflow |
|----------|------|--------------|
| `CNINFO_FETCH_MAX_RETRIES` | 3 | valuation / cninfo_backup |
| `CNINFO_FETCH_BACKOFF_SECONDS` | 2 | valuation / cninfo_backup |
| `CNINFO_WARMUP_DELAY_SECONDS` | 4 | cninfo_backup |

## 3. Workflow 触发时间(UTC / 北京时间)

| Workflow | cron(UTC) | 北京时间 | 说明 |
|----------|-----------|---------|------|
| `refresh_archive.yml` | `15 7 * * 1-5` | 15:15 | 板块日报前刷新归档(指数/国债/汇率/转债指数) |
| `valuation.yml` | `31 7 * * 1-5` | 15:31 | 市场估值 |
| `rotation.yml` | `34 7 * * 1-5` | 15:34 | 资产轮动 |
| `convertible.yml` | `37 7 * * 1-5` | 15:37 | 转债行情 |
| `coal.yml` | `40 7 * * 1-5` | 15:40 | 煤炭日报 |
| `commodity.yml` | `50 7 * * 1-5` | 15:50 | 商品极值 |
| `preview.yml` | `7 8 * * 1-5` | 16:07 | 静态校验(板块日报后) |
| `cninfo_backup.yml` | 多段(见文件) | 01:00–05:00 预热分片,其余重试 | 巨潮财报缓存预热/重试 |

5 板块错峰 3 分钟,末尾 `git pull --rebase` 后提交 `data/state` `data/archive`,避免并发 push 冲突。

## 4. 本地运行

```powershell
pip install -r requirements.txt
Copy-Item .\config\env.example .\.env.local   # 填入凭据(不入 git)

python -m src.valuation.run        # 单板块发信
python -m src.preview.generate     # 生成 5 个 preview/*.html
python -m src.preview.verify       # 静态校验 -> preview/verify_report.md
```

本地 pytest 必须 `--basetemp=.pytest_tmp`(系统 Temp 目录权限受限):
```powershell
python -m pytest --basetemp=.pytest_tmp
```

## 5. 旧仓库归档(手动)

`jisilu_ggx` 与 `monitor_drawdown` 已整合进本仓库。迁移完成后到这两个旧仓库 **Settings → 拉到底 → Archive this repository** 归档;建议在其 README 顶部加一行:

> ℹ️ 本仓库已迁移至 [market-daily](https://github.com/fadecat/market-daily),不再维护。

## 6. 数据目录(靠 git 持久化)

| 路径 | 内容 |
|------|------|
| `data/state/*.json` | 运行状态(持仓/净值/去重/上次日期) |
| `data/archive/` | 历史归档(指数 EOD/股息率/估值分位/国债/汇率/果仁快照) |
| `data/cb_index_history.json` | 转债等权指数(非股票估值) |
| `data/cninfo/` | 巨潮财报缓存 |
| `data/dividend_universe/` `data/whitelist/` | 高股息池/白名单 |

`preview/` 已 gitignore(仅留 `.gitkeep`),CI 上传为构建产物。
