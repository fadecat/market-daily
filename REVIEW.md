# market-daily 迁移 Review 报告

> **审查范围**:`D:\gitub_codes\market-daily` 全仓库,按 5 个邮件板块 + 公共层/CI 拆分,覆盖**数据获取 / 数据备份 / 流程编排 / Workflow / 测试**五个维度。
> **方式**:5 个 subagent 分头读代码确认(估值·轮动·转债 用 kimi-k3;煤炭+商品·公共层 用 deepseek-v4-flash),并对照源仓库(`commodity-monitor-days`、旧 `monitor_drawdown`)核移植回归。
> **测试基线**:511 passed(本地 `--basetemp=.pytest_tmp`)。
> **日期**:2026-08-07

---

## 一、总览

| 板块 | 高 | 中 | 低 | 最核心问题 |
|---|---|---|---|---|
| 市场估值 valuation | 2 | 3 | 6 | 高股息二次筛选未接入邮件;fx 归档翻倍 |
| 资产轮动 rotation | 0 | 2 | 6 | 重回填静默重置净值;发信失败不报警 |
| 转债行情 convertible | 1 | 3 | 4 | **tempdir 提前销毁致每日邮件破图** |
| 煤炭日报 coal | 0 | 1 | 2 | 旧 state 未迁移 -> 首跑重发 |
| 商品极值 commodity | 1 | 1 | 3 | `skip_if_no_today_data` 发信守卫丢失 |
| 公共层 + 预览 + CI | 1 | 2 | 5 | `_record_key` Timestamp bug(fx 翻倍根因);CI 缺 concurrency/timeout |

**4 个高危(P0)** + 6 个中危(P1)需优先处理;另有若干跨板块共性问题与拆分残留。

---

## 二、P0 高危问题(建议立即修)

### P0-1 · fx 归档记录翻倍(公共层根因 × 估值触发)
- **根因**:`src/common/storage.py:93-99` `_record_key` 对 `pd.Timestamp` 调 `str()` 得 `"2010-08-23 00:00:00"`(空格),而存量归档记录是序列化时 `isoformat()` 写出的 `"2010-08-23T00:00:00"`(T)。两 key 永不相等 -> `merge_archive` 同日保留两条。
- **触发**:`src/valuation/refresh_archive.py:106-122` `refresh_fx_dataset` 显式 `pd.to_datetime(df["日期"])` 把日期列变 Timestamp;`bond_10y` 因未做 to_datetime 而 0 重复,反向印证。
- **实测证据**:`data/archive/fx/usd_cnh.json` **8248 条记录、4123 个日期各出现 2 次**(恰好 2 倍)。每次 refresh 还因 `merged != existing` 产生无意义 commit。
- **建议**:公共层 `_record_key` 对 datetime/Timestamp 归一化(如 `str(value)[:10]` 或检测 `hasattr(value,"date")` 取 date);同时在 `refresh_fx_dataset` merge 前 `df["日期"] = df["日期"].dt.strftime("%Y-%m-%d")` 双保险;并清理已翻倍数据。补一条 Timestamp key 的 round-trip 测试。

### P0-2 · 转债日报每日内嵌图丢失
- **位置**:`src/convertible/run.py:95-106`
- **问题**:`with tempfile.TemporaryDirectory() as tmpdir:` 块在 `_build_sections(...)` 后就退出,而 `email.send_email(...)` 在 with 块**外**调用。转债指数图等落在 `tmpdir` 下,with 退出即被清理;`build_message` 检查 `Path(path).exists()` 为 False 后只打印 `[WARN] 跳过`(`common/email.py:166-168`),不报错。
- **矛盾**:docstring(L96)自己写着"tempdir 须存活到 send_email 读图完成,故发信在 with 块内",与实现相反。
- **影响**:**每天发出的转债日报里指数图(及可能的 screening 图)全是破图**,且静默无告警。
- **建议**:把 `subject/html/ok = email.send_email(...)` 三行移进 with 块(参照 `valuation/run.py:226-241`、`coal/run.py:39-66`)。

### P0-3 · 高股息二次筛选未接入邮件链路
- **位置**:`src/valuation/dividend/render.py:251-274` `build_section`
- **问题**:旧仓 `main.py:1481` 在 `prepare_dividend_email_data` 之前调 `filter_dividend_rows_by_secondary_rules(data)`(国资白名单 + 行业排除 + TTM 归母净利 ≥10 亿);新仓 `build_section` 只 `fetch_data -> prepare_dividend_email_data`,`filter.py` 整组函数在邮件链路**无任何调用方**(仅 cninfo_backup 用)。
- **影响**:① 邮件主表展示集思录原始返回(最多 rp=500 只,不过白名单/行业/TTM);② 主表「TTM归母净利(亿)」列恒为空(只有 filter 才写入 cell);③ 规则文案"筛选后剩余 N 只"永不出现;④ 旧仓 `send_fetch_failed_alert` 财报抓取失败告警也随之消失。
- **为何测试没发现**:`filter.py` 测试全在,但无任何测试断言 `build_section` 调用它,`test_valuation_dividend_render.py` 的 build_section 用例直接 mock 掉 fetch/prepare。
- **建议**:`build_section` 的 `fetch_data` 之后插入 `filter_dividend_rows_by_secondary_rules`,补编排测试。

### P0-4 · 商品极值 `skip_if_no_today_data` 发信守卫丢失
- **位置**:`src/commodity/run.py:30-43`
- **问题**:`skip_if_no_today_data` 被 config 解析但**从未使用**。源仓库 `scripts/run_daily_monitor.py:341-349` 在发信前检查 `has_today_data`,无今日数据(长假/akshare 全体返旧数据)时直接 return 不发;`.claude/plan.md:38` 也明确要求该守卫。移植后丢失。
- **影响**:akshare 全体返旧数据时,会发一封以旧日期为 subject 的"日报"。
- **建议**:`run_send` 加 `today_cn = datetime.now(ZoneInfo("Asia/Shanghai")).date()`;若 `cfg.skip_if_no_today_data and not any(r.error is None and r.latest_date == today_cn for r in results): return 0`。补对应单测。

---

## 三、P1 中危问题

### P1-1 · 轮动重回填静默重置净值/截断历史
- `src/rotation/strategy.py:377-382`:`last_run_date` 掉出 50 行窗口(约 10 周)时整体 backfill,`portfolio_nav` 重置为 1.0、`holdings_history` 被截断,**历史净值曲线和累计收益静默丢失**(仅一行 warning)。rotation 无 archive,state 是唯一持久化。
- 建议:重回填时保留旧 history 续接旧净值,或增大 `rp`。`replay_forward`/`backfill` 完全无测试,正是此路径漏网。

### P1-2 · 轮动/转债发信失败不报警(跨板块)
- `src/rotation/run.py:46-54` 与 `src/convertible/run.py:105` 的 try 块都**未覆盖 `email.send_email`**,SMTP 失败时异常抛出但**不发 `notify_alert`**,只能靠 GitHub 失败通知。而 valuation/coal/commodity 三板块都把发信包进 try + alert,约定不一致。
- 建议:扩大 try 覆盖 send_email,或外层补 `notify_alert`。

### P1-3 · 估值 `assemble_email_html` 图表行错位
- `src/valuation/render.py:632-643`:第一个循环 `blocks.append(block)` 会跳过空 block(无 PE/PB 时返回 ""),第二个循环却用 `enumerate(blocks)` 的序号 `i` 去索引**未过滤的** `items[i]` 取 code。中间任一 item 渲染为空,其后所有 PE 分位图都挂到错误指数名下,cid 与图错位。
- 建议:改 `for item in items:` 单循环,block 与 chart 同一 item 取 code。

### P1-4 · 估值 detail 接口无归档回退
- `src/valuation/fetch.py:853-854`:`fetch_index_detail` 失败时整个标的被跳过,即使 `data/archive/index_dividend_ratio|index_valuation_percentile/{code}.json` 有新鲜归档也用不上(detail 串行在带回退的接口之前)。
- 建议:detail 失败时用 `refresh_archive.resolve_index_code` 同款逻辑从 url 提取 indexCode,继续走股息率/分位归档回退。

### P1-5 · 转债科创板正股 IRM 静默漏掉
- `src/convertible/irm/query.py:213-218`:平台路由只认 `00/30->cninfo`、`60->SSE`,而筛选 `CB_ALLOWED_MARKETS` 含 `shkc`(科创板,688xxx),落入 `return []`,科创板转债正股的董秘互动被静默漏掉。
- 建议:补 `68->SSE`(上证 e 互动覆盖科创板)。

### P1-6 · 转债 `is_force_redeem_triggered` float 崩溃
- `src/convertible/screening/strategy.py:147-149`:直接 `float(c.get("sprice",0) or 0)`/`float(c.get("force_redeem_price",999) or 999)`,未用本文件 `to_float`。集思录返回 `"-"` 时 `float("-")` 抛 ValueError -> 每行渲染都调(`render.py:121`)-> 主 section 崩 -> 整板告警中止。
- 建议:改 `to_float(..., default=...)`。

### P1-7 · 煤炭旧 state 未迁移 -> 首跑重发
- 旧仓 `monitor_drawdown/data_state/cctda_coal_daily.json` 未拷入 market-daily `data/state/`(当前只有未跟踪的 `cb_three_low.json`)。coal 按 `article_url` 去重,首跑 state 缺失 -> 把订阅者已收到的最新一期日报重发一次。
- 建议:一次性把旧 state 文件拷入并提交。

### P1-8 · 转债三低/指数图 fetch 无重试
- `src/convertible/three_low/strategy.py:130-150` `fetch_cb_list`/`fetch_redeem_list` 未包 `alerts.run_with_retry`(screening 的 `fetch_cb_data` 包了),单次 timeout=15,瞬时抖动直接失败 -> 辅 section 当天缺席。建议统一包重试。

---

## 四、按板块详述

### 1. 市场估值 valuation

**数据获取**
- 【中】P1-4 detail 接口无归档回退(见上)。
- 【低】`fetch.py:699-708` 10Y 国债 live 路径"接口成功但缺列"返回空 DataFrame 不抛异常,空数据不触发归档回退且标 `live`,股债收益差静默消失。建议 live 结果为空视同失败走归档。
- 【低】`fetch.py:390-393` `raise_for_status()` 在 `run_with_retry` 之外,HTTP 4xx/5xx 永不重试(`is_retryable_error` 里的 502/503/504 分支对此路径是死代码)。
- 【低】`common/jisilu.py:82-124` 登录失败一律返回空串仅 `logger.error`,CI 无 handler 时排障信息少;登录请求本身未包重试。
- 集思录账密登录/cookie 复用、东财 cookie 懒读、巨潮 bundle 级退避重试均移植正确。

**数据备份**
- 【高】P0-1 fx 归档翻倍(见上)。state(`last_send_date`)、guorn_meta 快照(content_hash 不包信封,兼容旧 9 份)、cninfo manifest/日期双写、content_hash 去重均正确。
- 【中】`valuation.yml:47` 提交步骤只 `git add -A data/state data/archive`,东财补充池实时抓巨潮写的 `data/cninfo/` 从不提交 -> 新入池股票每天重复实时抓(限流风险)。建议提交步骤加 `data/cninfo`(与 cninfo_backup.yml 一致)。
- 【低】`dividend/cninfo_cache.py:38,204` `is_snapshot_fresh`/`CNINFO_CACHE_MAX_AGE_DAYS` 在 src 内无调用方(死代码 + 误导配置)。
- 【低】`cninfo_backup.py:152-204` 默认 backup 模式是写 `data/dividend_universe` 的唯一入口,但 workflow 只跑 `--warmup`/`--retry`,`data/dividend_universe` 自迁移后停止更新(非回归,遗留死路径)。

**流程编排**
- 【高】P0-3 高股息二次筛选丢失(见上)。
- 【中】P1-3 `assemble_email_html` 图表错位(见上)。
- 【低】`run.py:229-233` + `valuation.yml:6` 静默守卫 + 单日单次(北京 15:31):易方达估值分位 JSON 常晚间更新,15:31 时若仍是 T-1 且等于已发日期则当日静默退出,无补发档位(旧仓有 11:37/15:07/20:17 三档)。建议加一档晚间 cron 复用守卫天然幂等。
- 【低】`dividend/render.py:258-265` 集思录返回空 rows(cookie 过期典型症状)旧仓有专门告警,新仓只安静渲染"暂无数据"。建议 rows 为空时 `notify_alert`。
- 【低】`run.py:236-241` `send_email` 成功恒 True、失败抛异常,`ok` 的 False 分支与 `return 0 if ok else 1` 是死代码(共性,见五)。
- 主/辅 section 关系、10Y 国债循环外取一次、单标失败跳过、tempdir 存活到发信完成、`--preview` 不走守卫均正确。

**Workflow**
- 【中】`cninfo_backup.yml:38-47` 注释"全成功时空跑不抓取"不准:`run_incremental_warmup` 在判断 `only_not_checked_today` 前必先拉集思录高股息 + 东财补充池;重试档 cron 每天 8+2 次,每次都打这两个外部接口(浪费 + 限流暴露)。
- 【低】vs 旧仓:手动 `workflow_dispatch` 非凌晨触发时旧仓跑全量 `--warmup`,新仓落入 `--retry`(只补当日未成功标的),失去手动全量重建缓存入口;旧仓 warmup 有 `timeout-minutes:90`+`continue-on-error`,新仓无 timeout。
- 【低】`cninfo_backup.yml:5-8` cron 注释北京星期映射写错(UTC `17-21 * * 0-4` = 北京周一至周五,非"周日-周四"),行为正确仅注释误导。
- valuation.yml cron(UTC 07:31 = 北京 15:31,与 refresh_archive 15:15 错峰 16 分钟)、secrets/vars、`contents: write`、`pull --rebase` 后 push 均正确。

**测试**
- 覆盖整体好:15 个测试文件约 4000 行,网络层全 monkeypatch/注入 fake,未发现触网。
- 【中】缺口与两个真 bug 一一对应:① 无测试断言 `dividend.build_section` 调二次筛选;② `test_valuation_refresh_archive.py:123-135` fx 用例 mock 掉 `merge_archive`,无法暴露 key 类型 bug;③ 无 `assemble_email_html` 空 block 错位用例。
- 【低】无 conftest.py 全局禁网守卫(建议 pytest-socket)。

**总评**:骨架移植质量高,公共层契约/归档回退/卡片渲染/守卫忠实还原且测试扎实;但 1 个高危功能遗漏(高股息二次筛选) + 1 个高危存量数据 bug(fx 翻倍)需优先修。

---

### 2. 资产轮动 rotation

**数据获取**
- 【低】`strategy.py:91` `fetch_detail_history` 直接 `session.post` 未走 `run_with_retry`(同模块 `etf_data.py` 对 akshare/tickflow 全包了),集思录偶发 5xx/超时即整 run 失败。建议套重试。
- 【低】`common/jisilu.py:79` `fetch_etf_list` 固定 `rp=25` 只取前 25 行,universe 里 ETF 若不在前 25 实时补价 miss(有 eastmoney 逐码兜底不致命)。
- 【低】`strategy.py:159` 3 只 QDII 永不补当日价只能 ffill -> 信号日 ETF 用 T 日价、QDII 用 T-1 日价,20 日动量口径跨标的不一致(忠实移植,固有瑕疵,建议至少注释注明)。
- 【低】`etf_data.py:199` code 已带 sh/sz 前缀时两元素相同重复请求(无害浪费);`strategy.py:84` `cookie` 形参死参数。

**数据备份**
- 【中】P1-1 重回填静默重置净值(见上)。
- 【低】rotation 完全无 archive 落盘(对比 valuation/convertible 都有 `merge_archive`),state 是唯一持久化,放大重回填代价。
- 【低】`data/state/etf_rotation_20d.json` 当前未提交,首次 CI 前 `preview/verify.py:97` 会每天报"资产轮动状态缺失"(首跑自愈)。
- 去重(`holdings_history` 按日期增量 append、`latest_date <= last_date` 跳过)、`last_run_date`/`next_holding` 维护正确。

**流程编排**
- 【中】P1-2 发信失败不报警(见上)。
- 【低】`run.py:31-33` 策略无数据 `return 1` 让 workflow 失败但不发 webhook(与异常路径不一致,全数据源挂掉恰最该报警)。
- 【低】`run.py:35-38` 发信守卫用 `len(holdings_history) <= prev_count` 判断无新交易日,与重回填联动有漏洞:重回填后 history 只剩窗口内 ~50 条,若少于旧累计条数,即使净值已重置也跳过邮件,用户无感知。建议改用 `last_run_date` 比较或对重回填强制发信。
- 轮动逻辑本身无未来函数实现正确(T-1 信号决定 T 日持仓、增量重放从 `portfolio_nav` 续接、QDII/ETF 两套 cell 解析与旧版一致)。发信时机 15:34 合理。

**Workflow**
- 无明显问题。cron `34 7 * * 1-5` = 北京 15:34,与各板块错峰;secrets 与代码 `env.require` 一一对应;`contents: write`;state commit 带 `pull --rebase`。
- 【低】缺 `timeout-minutes`/`concurrency`(仓库级通病),schedule 与手动 dispatch 叠加可并发双跑重复发信。

**测试**
- 【中】`replay_forward`/`backfill`(增量续接、无未来函数不变量、净值连续性)完全无用例;`run_strategy` 增量/重回填两分支无覆盖--正是"重回填丢净值"漏网处;`run_send` 长度守卫无测试。
- 【低】`fetch_detail_history` etf/qdii 两套 cell 字段解析无 fixture 测试;仓库无跑 pytest 的 CI 工作流(仓库级缺口)。
- 好的方面:9 用例全纯函数不触网,`_normalize_dataframe`/symbol 前缀/对齐/回撤/report+render 链路有覆盖;`render.build_email_text`(`render.py:49`)无调用方,死代码。

**总评**:移植整体质量高,策略/补价/发信守卫与旧版逐行对应,common 层契约规范,workflow 无误。主要风险:重回填静默重置净值且无测试覆盖,发信失败/无数据两条路径不发 alert。

---

### 3. 转债行情 convertible

**数据获取**
- 【中】P1-5 科创板 IRM 漏掉(见上)。
- 【中】P1-6 `is_force_redeem_triggered` float 崩溃(见上)。
- 【中】P1-8 三低 fetch 无重试(见上)。
- 【低】`common/jisilu.py:104-124` 登录单次 POST 无重试(主 section + 三低共同前置)。
- 【低】`irm/query.py:246-247` + `screening/archive.py:324-336` IRM 去重后正股(最多 50 只 ×2 POST)与下修归档(每债 2 GET)无 sleep/节流,有触发限流风险(有兜底,降级非崩溃)。

**数据备份**
- 【低】`data/cb_bonds/` 空残留目录(实际归档已迁 `data/archive/cb_bonds`),建议删除免误导。
- 其余无问题:`data/state/cb_three_low.json` 签名去重 + 同日覆盖重算正确;`data/cb_index_history.json` content_hash 去重,由 refresh_archive.yml 每日 15:15 刷新提交;`data/archive/cb_bonds` 由 convertible.yml 提交覆盖;三低状态键 `holdings_history`(无 `next_holding`,与代码一致)。

**流程编排**
- 【高】P0-2 tempdir 破图(见上)。
- 【中】P1-2 发信失败不报警(见上)。
- 【中】`three_low/run.py:42-47` 经 `run.py:111-115` 板块 `--preview` 会真实跑 `run_strategy` 并 `save_state`(推进持仓/净值历史),screening 预览也写 `data/archive/cb_bonds`。CI 预览(contents: read)无副作用,但本地跑预览污染模拟盘状态。three_low 自己的 `run_preview` 是离线(只 load_state),板块预览路径却在线,语义不一致。
- 【低】重复告警:`three_low/run.py:45-47` 和 `index_chart/run.py:32-34` 都"先 notify_alert 再 raise",板块 `run.py:52-53/70-71` 捕获后又 alert,同一故障发两条企业微信。建议子 section 只 raise,告警统一由板块层做。
- 【低】`three_low/run.py:60-69` 净值图生成失败时 HTML 仍含 `src="cid:cb_three_low_nav_chart"` 但 inline_images 为空 -> 邮件破图(预览版有"净值图未生成"兜底,邮件版没有)。建议图失败时替换/移除 img 标签。
- 筛选失败中止整板、辅 section None 略过/异常告警继续、`_cid_to_data_uri` 替换格式吻合、三低签名守卫节假日静默均符合设计。

**Workflow**
- 【低】无 `concurrency` 组:schedule 与手动 dispatch 叠加(或与 refresh_archive 15:15 提交撞车)时 `git pull --rebase` 后 push 仍可能因并发提交失败且无重试。建议加 `concurrency: convertible-${{ github.ref }}`。
- 其余无问题:cron `37 7 * * 1-5` = 北京 15:37 收盘后;secrets 齐全;`contents: write`;commit 范围 `data/state data/archive` 覆盖 cb_three_low.json 与 cb_bonds;cb_index_history.json 由 refresh_archive.yml 提交无遗漏。

**测试**
- 【中】`run.py` 板块编排层零覆盖--`_build_sections`/`_cid_to_data_uri`/`run_send` 均无测试,tempdir 破图 bug 正是这一层漏网。建议 monkeypatch 各 section builder + send_email 做纯本地编排测试。
- 【中】`three_low/strategy.py` `run_strategy` 状态机(首跑播种/新交易日追加/同日覆盖重算/签名一致跳过)无任何测试(全板块最易出净值错误的逻辑);`screening/archive.py` `refresh_cb_adjust_archives`(失败回退缓存 + 告警)无测试。
- 【低】本地 Windows 4 个 `tmp_path` 用例报 `PermissionError`(环境问题非代码);61 passed,均不触网。

**总评**:数据层/渲染层移植质量较好,workflow 与持久化接线完整;但板块编排层 `run.py` 是最薄弱一环--tempdir 提前销毁致每日邮件破图(高危、每天发生)、发信失败无告警、预览污染状态,且编排层零测试。优先修 `run.py:95-106` 并补编排测试。

---

### 4. 煤炭日报 coal

**数据获取**
- 【低】`cctda.py:153` `download_report_images` 把任意格式图片统一存 `page_XX.png`,CCTDA 若挂 JPEG 则文件内容是 JPEG 却署名 png;`common/email.py:_image_subtype` 按后缀判 subtype=png,preview data URI 也写 `image/png`。多数客户端能嗅探容错,但与真实 MIME 不符(旧仓同样如此,非本次引入)。

**数据备份**
- 【中】P1-7 旧 state 未迁移致首跑重发(见上)。
- 【低】`run.py:41,61` `content_hash` 计算并存入 state,但去重只看 `article_url`,hash 字段纯死数据(旧仓如此,移植保留)。

**流程编排**
- coal 侧编排(列表->详情->物化->cid 内联发信->写 state,异常时 `notify_alert` + raise 使 workflow 变红)核对无误;cid `report_page_N` 与 `inline_images` 键一一对应;preview base64 内嵌正确。
- 【低】`run.py:14` 用绝对导入 `from src.common import ...` vs commodity 用相对导入 `from ..common import ...`,风格不统一(功能均正确)。
- 【低】`run.py:39-40` `ok = email.send_email(...)` 后 `return 0 if ok else 1` 死分支(共性)。

**Workflow**
- cron 错峰(coal 07:40 UTC = 北京 15:40 收盘后)、secrets、`contents: write`、`pull --rebase` 均与 README/DEPLOY.md 一致,无问题。coal 10 分钟前于 commodity 启动,两板块 push 冲突由 rebase 兜底,可接受。

**测试**
- 【低】不触网,覆盖到位:列表/详情解析、skip、cid、base64、hash。拆分后 import 无残留:`preview/generate.py:21-22` 正确指向 `src.coal`/`src.commodity`,`test_preview_generate.py` 间接覆盖模块级导入正确性。
- 【低】缺口:`coal.run.run_send` 去重主流程无 mock 测试。建议补 run_send 级单测防回归。

**总评**:拆分干净,核心抓取/去重/cid 内嵌逻辑正确;唯一实质问题是旧 state 未迁移会致首跑重发,补上即可。

---

### 5. 商品极值 commodity

**数据获取**
- 【中】移植把源仓库 degrade 机制(`max_run_seconds=240` 运行时上限、fail-ratio 降级)整体丢弃,且 akshare 调用(`futures_foreign_hist`/`futures_zh_daily_sina`)无超时无重试。某品种网络挂起时整个 75 品种扫描跟着挂,CI 无 `timeout-minutes` 兜底。建议给 commodity.yml 加 `timeout-minutes: 20`,或给 `evaluate_symbol` 的 fetch 包 `run_with_retry`。
- 【低】`core.py:114` `stale_days` 用 CI 本地(UTC)日期。15:50 北京 = 07:50 UTC 当天一致正常;但外盘品种周一早上 latest 为周五(stale=3),叠加欧美假期 >5 天会被 reporting stale 过滤误剔除(源仓库同逻辑,非回归)。

**数据备份**
- commodity 无 state 属正确设计(日频扫描无需去重),此维度无问题。

**流程编排**
- 【高】P0-4 `skip_if_no_today_data` 守卫丢失(见上)。
- 【低】`config.py:39,115` + `run.py:34` `cfg.max_stale_days`(yaml 配 10)成死配置:`build_email_html` stale 阈值硬编码默认 5,run.py 未传。yaml 里 `max_stale_days: 10` 具误导性。建议 `build_email_html(results, cfg, stale_days_threshold=cfg.max_stale_days)` 或删配置项。
- 【低】`run.py:39-40` `return 0 if ok else 1` 死分支(共性)。
- 【低】导入风格相对导入 vs coal 绝对导入不统一(见上)。

**Workflow**
- 【低】`commodity.yml:32-44` commodity 不写任何 state/archive,"提交 state 变更"步骤永远 no-op,commit message「商品极值 state 快照」名不副实。留着无害,建议删除免误提交其他板块残留。
- 【低】无 `timeout-minutes`,配合无超时 akshare 调用极端挂起会跑满 GitHub 默认 6h。建议加 `timeout-minutes: 20`。
- cron 错峰(07:50 UTC = 北京 15:50)、secrets、`contents: write`、`pull --rebase` 均一致。commodity 扫描 ~5-9 分钟,coal 10 分钟前启动,可接受。

**测试**
- 【低】不触网,覆盖到位:分位数、min_points、config 校验(含真实 yaml 75 品种断言)、板块分组、红绿单元格、stale 过滤、SM/SM0 消歧。拆分后 import 无残留。
- 【低】缺口:`commodity.core.evaluate_symbol`/`run_scan`(monkeypatch `fetch_history` 即可)与 `run.run_send` 的 subject/日期逻辑(正是 skip_if_no_today_data 应挂位置)无测试。建议补 run_send 级单测防 P0-4 回归。

**总评**:拆分干净、无 import 残留,commodity 核心逻辑(分位数/归组/渲染)与源仓库逐行一致、无回归;真正问题集中在编排层:`skip_if_no_today_data` 守卫丢失(高)+ 旧 coal state 未迁移(中)。补这两点 + 给 workflow 加超时即可放心上线。

---

### 6. 公共层 + 预览校验 + CI

**公共层契约/数据获取**
- 【高】P0-1 `_record_key` Timestamp bug(fx 翻倍根因,见上)。
- 【中】P1-2 rotation/ convertible `send_email` 未被 try 包裹(见上)。
- 【低】`email.py:178-199` `send_email` 成功 return True / 失败抛异常,无 `return False` 路径,各板块 `else 1` 死分支(共性)。
- 【低】`jisilu.py:119-121,94` 登录异常全吞成 `return ""`,网络故障被报告成"账号密码错"误导排查。建议异常类型透传或加进 message。
- env.py/alerts.py/fonts.py/whitelist.py 无明显问题:`env.get` 优先 `os.getenv` 再回退 `.env.local`,`require` 缺失抛 RuntimeError;`run_with_retry` 对 TypeError 不重试合理;`notify_alert` 无 webhook 仅日志符合预期;`fonts.resolve_font` 多字体回退正确。

**数据备份/持久化**
- 【高】P0-1(见上)。
- 【低】`storage.py:166` `if merged == existing: return None` 用列表相等判定,`merged` 按 key 排序、`existing` 原顺序,历史遗留未排序时多一次序列化开销(无功能错误,`write_archive_file` 文本比对兜底)。
- content_hash 去重、`save_state`/`load_state`、`merge_archive` filename 推导均正确;`_json_default` 对 datetime/date/Timestamp 走 isoformat 序列化一致。各板块写不同 state 文件,archive 仅 refresh_archive 写,实际无写写冲突,无需文件锁。

**预览/校验**
- 【低】`preview/generate.py:62` docstring 写"全部 4 板块",实际 `_BOARDS` 5 个条目 -> 改"5 板块"。
- 【低】`preview.yml:5` cron 注释"4 板块日报跑完"-> 改"5 板块"。
- 【低】`preview.yml:43-44` "静态校验"步骤无 `if`,3 种 mode(verify/generate/both)都跑校验;input 把 generate 标"仅生成预览"与实际不符。建议加 `if: ${{ github.event.inputs.mode != 'generate' }}`。
- 【低】`test_preview_generate.py:14` `_patch_boards` docstring"4 个 board"实际 patch 5 个 -> 改"5 个"。
- generate.py 5 板块编排正确(coal 返回 int、其余返回 Path 分支处理与测试一致);verify.py `_STATE_SPECS` 列 4 个有 state 板块(commodity 无 state 不列正确);`check_archive_dates` 用 `str(d)[:10]` 截日期,**已能捕获 fx 翻倍**;`check_preview_html` cid 残留检测正确;preview.yml 上传产物 + `if-no-files-found: ignore` + `retention-days: 14` 合理。

**CI/Workflow 综述**
- 【中】全部 8 个 workflow 均无 `concurrency:`、无 `timeout-minutes:`。集中在 UTC 07:15-08:07 窗口,末尾都 `git pull --rebase && git push`,错峰 3 分钟降低概率但某次偏快/偏慢时 push 阶段重叠第二个会 non-fast-forward 失败且无重试;`cninfo_backup.yml` 最晚 15:07 与 refresh_archive 15:15 只差 8 分钟也有重叠风险。建议加 `concurrency: { group: ci-push, cancel-in-progress: false }` 串行化 push,或 commit 步骤加 `git push || (git pull --rebase && git push)` 重试;各 job 加 `timeout-minutes: 20`。
- 【低】`commodity.yml` state commit 步骤多余(见 commodity 维度 4)。
- 【低】`.github/actions/setup-python-cjk/action.yml:2` description"4 个板块 workflow 复用"实际 8 个 -> 改"各 workflow 复用"。
- secrets/variables 清单:DEPLOY.md 与各 yml 一一对照无缺漏;state commit 步骤在 6 个相关 workflow 一致(`fetch-depth:0`+`contents:write`+`git config`+`git add`+`git diff --cached --quiet` 短路+`pull --rebase`+`push`);cron 错峰表(refresh 07:15->valuation 07:31->rotation 07:34->convertible 07:37->coal 07:40->commodity 07:50->preview 08:07)合理,commodity 多等 10 分钟给扫描留余量;`preview/*` gitignore 正确。

**测试**
- 【中】`test_common.py` storage 测试未覆盖 `_record_key` 对 `pd.Timestamp` 处理(只用字符串 key),fx 翻倍 bug 长期潜伏。建议加 Timestamp key 的 merge 测试。
- 【中】无 `test_convertible_run.py`/`test_rotation_run.py`,`run_send` 完全未测 -> P0-2/P1-2 漏网。`test_valuation_run.py` 则较完整。建议补 convertible/rotation run_send 测试。
- 【低】`test_preview_verify.py` `test_run_all_pass` 只造单条 fx 记录,未测 fx 翻倍场景;`test_archive_duplicate_dates` 测通用重复未针对 Timestamp/str 混入。
- requirements.txt 依赖齐全(pycryptodome/requests/pyyaml/pandas/matplotlib/akshare/beautifulsoup4/PyMuPDF/Pillow/openpyxl/pytest);httpx 可选依赖合理。测试不触网。

**总评**:公共层设计干净,但 `_record_key` 对 Timestamp 的天真 `str()` 是 fx 翻倍直接根因;convertible/rotation 对 `send_email` 异常包裹与 tempdir 生命周期与其它三板块不一致,致转债邮件无图、轮动/转债发信失败不报警两个真实缺陷。CI 缺 concurrency/timeout 是中长期风险点。

---

## 五、跨板块共性问题

1. **发信失败不报警**(轮动/转债):try 未覆盖 `send_email`,违反"webhook 只做异常报警"统一约定(P1-2)。
2. **`send_email` 死分支**(估值/煤炭/商品/公共):`return 0 if ok else 1` 的 `else 1` 永不可达,`send_email` 失败必抛异常。建议统一语义删死分支。
3. **CI 缺 `concurrency`/`timeout-minutes`**(全仓库 8 个 workflow):并发 push 失败、挂起跑满 6h 风险。
4. **"4 板块"字样残留**(拆分文档):`preview.yml:5`、`generate.py:62`、`setup-python-cjk/action.yml:2`、`test_preview_generate.py:14` 四处,均低危。
5. **commodity workflow 多余 state commit**:无 state 却保留步骤 + commit message 名不副实。
6. **板块编排层测试缺口**:convertible/rotation `run_send` 零覆盖,valuation dividend `build_section` 编排无断言--三个高危/中危 bug 都因此漏网。
7. **集思录登录无重试 + 异常吞没**(`common/jisilu.py`):多板块共同前置,瞬时失败即整板中止,且网络故障被报成"账密错"。

---

## 六、修复优先级建议

**P0(立即,影响线上正确性)**
1. 修 `_record_key` Timestamp 归一化 + 清理 fx 翻倍数据 + 补 round-trip 测试(P0-1)
2. 修 convertible `run.py` tempdir 生命周期,send_email 移进 with 块(P0-2)
3. 修 valuation dividend `build_section` 接入二次筛选 + 补编排测试(P0-3)
4. 修 commodity `run_send` 挂回 `skip_if_no_today_data` 守卫 + 补单测(P0-4)

**P1(尽快,影响可靠性/可观测性)**
5. 拷入旧 coal state 文件(P1-7)
6. 轮动/转债 `send_email` 纳入 try + `notify_alert`(P1-2)
7. 修估值 `assemble_email_html` 图表错位(P1-3)
8. 修转债 `is_force_redeem_triggered` float 崩溃 + 科创板 IRM 路由(P1-5、P1-6)
9. 估值 detail 接口加归档回退(P1-4)
10. 轮动重回填保留旧净值 + 补 `replay_forward` 测试(P1-1)
11. 转债三低/指数图 fetch 包重试(P1-8)

**P2(择机,健壮性/文档)**
12. CI 加 `concurrency` + `timeout-minutes`(全仓库)
13. 修正"4 板块"残留 + commodity 多余 state commit + preview.yml mode 行为
14. valuation.yml 提交 `data/cninfo`、加晚间 cron 补发档
15. 补 convertible/rotation `run_send` 编排测试 + storage Timestamp key 测试
16. jisilu 登录包重试 + 异常透传;集思录空 rows 告警

---

## 七、总评

**移植质量整体高**:5 板块拆分干净、无 import 残留,commodity 核心逻辑与源仓库逐行一致无回归,公共层契约/归档回退/卡片渲染/守卫忠实还原,511 测试绿且不触网,workflow 模板一致、secrets 完整、cron 错峰合理。

**但编排层是薄弱环节**:4 个 P0 中 3 个(P0-2 转债破图、P0-3 高股息筛选丢失、P0-4 守卫丢失)都出在各板块 `run.py`/`build_section` 编排层,且这一层测试覆盖最薄(convertible/rotation `run_send` 零覆盖)。另 1 个 P0(P0-1 fx 翻倍)是公共层 `_record_key` 对 Timestamp 的类型处理疏忽,已在生产数据发生。

**建议路径**:先修 4 个 P0(均为线上正确性,每天发生),再补编排层测试锁死,最后清理 P2 共性问题。修完后可放心上线 5 板块日报。
