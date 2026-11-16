# A 股通达信实盘执行层

本专题说明如何在本仓库启用「AI 决策信号 → 订单意图 → 半自动/全自动提交」执行链路。`DecisionSignal` 仍只表示建议；真正交易走独立 `execution` 域。

## 能力边界

- 默认 `LIVE_TRADING_ENABLED=false`：确认或自动提交只走进程内纸面 broker，不连接通达信。
- `EXECUTION_CONFIRM_MODE=manual|auto` 可在 Web 系统设置或 `.env` 切换半自动 / 全自动。
- 通达信适配器仅支持 **本机 Windows** + 可选 `easytrader` 客户端自动化；交易解锁密码只用本机环境变量 `TDX_TRADE_PASSWORD`，不要交给 Chat / 云端 / Web 明文回显。
- `pytdx` 仍只是行情；Futu 集成保持只读持仓，不因本功能改变。

## 流程

1. 个股分析历史落库后提取 `DecisionSignal`。
2. `ExecutionOrchestrator` best-effort 映射为 `execution_order_intents`。
3. 风控门禁 fail-closed：市场 allowlist、可交易 action、名义金额/仓位上限、entry band、幂等键、实盘时段。
4. `manual`：状态 `pending_confirm`，在 Web「执行队列」或 API 确认后提交。
5. `auto`：风控通过后直接提交；仅当 `LIVE_TRADING_ENABLED=true` 且 `EXECUTION_BROKER=tdx` 时打通达信。

## 配置

| Key | 默认 | 说明 |
| --- | --- | --- |
| `LIVE_TRADING_ENABLED` | `false` | 实盘总开关 |
| `EXECUTION_CONFIRM_MODE` | `manual` | `manual` / `auto` |
| `EXECUTION_BROKER` | `tdx` | `tdx` 或 `paper` |
| `EXECUTION_MARKET_ALLOWLIST` | `cn` | 非匹配市场拒绝 |
| `EXECUTION_ALLOWED_ACTIONS` | `buy,add,sell,reduce` | 可交易 action |
| `EXECUTION_MAX_ORDER_NOTIONAL_CNY` | 空 | **必须显式配置**，否则拒单 |
| `EXECUTION_MAX_POSITION_PCT` | 空 | **必须显式配置**，否则拒单 |
| `EXECUTION_REQUIRE_ENTRY_BAND` | `true` | buy/add 需要 entry 区间 |
| `EXECUTION_MIN_PROFIT_PCT_TO_SELL` | 空 | sell 最低浮盈百分比；空=不启用 |
| `EXECUTION_MIN_PROFIT_CNY_TO_SELL` | 空 | sell 最低浮盈金额（CNY）；空=不启用 |
| `EXECUTION_MIN_PROFIT_PCT_TO_REDUCE` | 空 | reduce 最低浮盈百分比；空=不启用 |
| `EXECUTION_MIN_PROFIT_CNY_TO_REDUCE` | 空 | reduce 最低浮盈金额（CNY）；空=不启用 |
| `EXECUTION_PORTFOLIO_ACCOUNT_ID` | 空 | 可选组合账户，用于资金/持仓测算 |
| `EXECUTION_SYNC_PORTFOLIO_LEDGER` | `false` | 提交成功后是否回写组合成交 |
| `TDX_CLIENT_PATH` | 空 | 通达信客户端路径 |
| `TDX_ACCOUNT_ID` | 空 | 账户标识（非密码） |
| `TDX_TRADE_PASSWORD` | 空 | 仅本机 env，不进 config registry |

切换全自动示例：

```bash
LIVE_TRADING_ENABLED=true
EXECUTION_CONFIRM_MODE=auto
EXECUTION_BROKER=tdx
EXECUTION_MAX_ORDER_NOTIONAL_CNY=20000
EXECUTION_MAX_POSITION_PCT=10
TDX_CLIENT_PATH=C:\tdx\TdxW.exe
# 本机 shell 临时设置，勿写入仓库
# set TDX_TRADE_PASSWORD=******
```

## API

- `GET /api/v1/execution/intents`
- `GET /api/v1/execution/intents/{id}`
- `POST /api/v1/execution/intents/{id}/confirm`
- `POST /api/v1/execution/intents/{id}/reject`
- `POST /api/v1/execution/intents/{id}/cancel`

继承现有管理员会话鉴权。

## 仓位与价格（首期）

- 仅 `market=cn`。
- buy/add：限价取 `entry_low~entry_high` 中值；数量按 `min(可用资金*占比, 名义上限)/限价` 向下取整到手数（主板 100；688/689 为 200）。
- sell：清仓可卖手数；reduce：约减半。
- sell 与 reduce 盈利门槛分开配置：各自可配百分比和/或金额；同一动作内两项都配则需同时满足（AND）。按「限价相对持仓均价」估算；缺成本价则 fail-closed。
- 北交所等未可靠识别板拒绝下单。

## 故障排查

- 意图一直 `rejected` 且原因为 `max_notional_not_configured` / `max_position_pct_not_configured`：先配置金额与仓位上限。
- 实盘 `failed` + `Tongdaxin...Windows`：非 Windows 环境不能用 tdx adapter。
- `easytrader is not installed`：在本机安装可选依赖后再开实盘。
- 客户端升级、锁屏、弹窗会导致 UI 自动化失败；失败记 `failed` 并停止，不自动连打。
- 紧急停用：设 `LIVE_TRADING_ENABLED=false`，并把 `EXECUTION_CONFIRM_MODE` 改回 `manual`。

## 回滚

关闭上述开关即可停止新的真单；必要时 revert 执行模块相关提交。
