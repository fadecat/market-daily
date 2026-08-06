# market-daily 项目指令

## 项目背景
整合自 `jisilu_ggx`(集思录筛选)+ `monitor_drawdown`(回撤/估值监控)两个历史仓库。产出 4 封板块邮件:市场估值 / 资产轮动 / 转债行情 / 商品极值。

## 架构约定(务必遵守)

### 公共层优先
`src/common/` 是所有板块的依赖,改动板块前确认 common 层已就绪:
- `env.py` - 配置加载(`.env.local` + 环境变量回退),统一读取方式,不要在板块里裸 `os.getenv`
- `jisilu.py` - 账密登录,对外 `get_cookie()`(板块常用)/ `make_session()`;**禁止**再用 `JISILU_COOKIE` / 硬编码 cookie
- `email.py` - SMTP 发信 + `compose_sections()` 多 section 聚合 + `render_markdown`/`render_table`
- `alerts.py` - `notify_alert()`(唯一报警 webhook)+ `run_with_retry()` 装饰器;网络层都要套重试
- `storage.py` - `save_snapshot`(content_hash 去重)/ `load_state`/`save_state`(`data/state/`)/ `merge_archive`(`data/archive/`)
- `fonts.py` - CJK 字体解析,所有 matplotlib 图表统一用

### 邮件形态
- 4 封邮件 = 4 板块,每封内部多 section 聚合(不是 1 脚本 1 邮件)。
- 板块 `run.py` 聚合各 section,任一 section 有新数据即发;全板块无新数据(节假日)静默退出。
- 日报走 SMTP;**webhook 只在板块跑挂时报警**,不再推日报。

### 数据持久化
- `data/state/*.json`(运行状态:持仓/净值/去重)、`data/archive/`(历史归档)、`data/cninfo/`(巨潮财报)、`data/cb_bonds/`(下修)、`data/dividend_universe/`、`data/whitelist/`、`data/cb_index_history.json`(转债等权指数)都靠 git commit 持久化。
- workflow 末尾必须保留 `Detect state changes -> Commit` 步骤(`fetch-depth: 0` + `permissions: contents: write`)。
- 写归档用 content_hash 去重,内容不变不写,避免无意义 commit。

### 命名陷阱
- `data/cb_index_history.json`(原 `market_temperature_history.json`)是**转债等权指数**,不是股票估值,归"转债行情"板块。别被旧名误导。

## 退役清单(不要迁移)
- 回撤监控(`monitor_drawdown.py` 的 etf/index 分支、`compute_drawdown`、`send_webhook`)
- `etf_rotation_v2_*`、`prototype_equity_bond_chart.py`
- 果仁网选股 `guorn_screen_client.py`(注意:果仁**行业估值**保留,在市场估值板块)
- 各类产物文件(`*.html` preview、`*.png`、`*.txt` 测试输出)

## 验证
- 改动后优先跑 `python -m src.preview.verify`(复用 `data/state/` 快照,不重跑全量)。
- 本地预览用 `python -m src.preview.generate <板块>`。
- 用户不喜欢重复验证:动数据前先看 `data/state/` 已有快照,别盲目重跑。

## 平台
- 开发环境 Windows,CI 跑 Ubuntu。matplotlib 中文图需 `fonts-noto-cjk`(composite action 统一装)。
- Python 3.10+。
