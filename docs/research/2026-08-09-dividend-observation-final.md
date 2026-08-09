# 红利观察图阶段性收尾说明

## 最终保留成果

- `preview/dividend_observation_930955.html`
- `data/research/dividend_observation_930955.json`
- `data/research/style_rotation_preview.json`
- `src/research/dividend_observation_chart.py`
- `src/research/dividend_observation_chart_preview.py`
- `tests/test_dividend_observation_chart.py`
- `tests/test_dividend_observation_chart_preview.py`

## 本轮研究结论

### 1. 最终产物定位

本轮最稳定、最可复用的成果，是围绕 `930955 红利低波100` 的本地观察图，而不是更大范围的风格回撤研究包。

观察图当前回答四件事：

- 当前价格位置与近五年回撤
- 绝对定价位置
- 利率相对吸引力
- 风格挤压状态

### 2. 价格与收益口径

当前观察图的价格层使用 `价格指数` 口径。

这对观察市场交易定价是合理的，但在红利指数的 `6-7月分红季`，价格口径会受到除权扰动；因此：

- `价格与回撤` 图可以继续使用
- `利率相对吸引力` 中的股息率差，在分红季需要谨慎解释

### 3. 风格挤压数据来源

`风格挤压` 不再依赖本地 archive 中不存在的 `399376/399373` 文件。

当前实现为：

- 优先读取本地 `data/research/style_rotation_preview.json`
- 本地文件不存在时，复用现有 `style_rotation` 实时抓取逻辑生成
- 再写回本地 JSON，供观察图复用

这使得观察图可以独立于 archive 存在。

## 清理原则

本次收尾删除了：

- 讨论阶段的 drawdown / style seesaw / event state 研究脚本
- 对应的阶段性 JSON 产物
- 讨论过程中的 spec / plan / 审稿记录

保留逻辑只围绕最终观察图成果展开，避免分支继续膨胀。

## 后续建议

如果后续继续推进，不建议在当前分支恢复大而散的研究包。

更合适的做法是：

- 以 `dividend_observation_930955` 为主线继续迭代
- 单独补充 tooltip、口径提示、全收益对照
- 如果以后要恢复更大的研究体系，另起分支重新组织
