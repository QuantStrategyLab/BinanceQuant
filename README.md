# BinanceQuant

`BinanceQuant` 是一个运行在币安现货账户上的自动化加密货币量化交易项目。  
项目以 `BTC` 为核心底仓，通过动态定投与分批止盈管理长期仓位，同时用主流山寨币趋势轮动争取超额收益。系统兼容币安现货与活期理财账户，支持自动赎回、USDT 现货水位维护、`BNB` 手续费燃料补仓、Telegram 推送以及 Firestore 状态持久化。

当前默认生产接入方式是：

- 上游项目 `CryptoLeaderRotation` 每月发布一次生产版 `core_major` 趋势池
- 本项目优先读取上游发布的当前生效池
- 若上游发布不可用，则自动回退到本项目内置静态趋势池

## 项目结构

- `main.py`
  实盘执行脚本，每小时运行一次。
- `backtest.py`
  研究与回测脚本，用于验证选池、排序与风控逻辑。
- `requirements.txt`
  Python 依赖清单。

## 策略总览

组合由两部分组成：

- `BTC` 核心仓
  使用估值与过热指标做动态建仓和分批减仓，目标是在长期上涨周期中持续积累 `BTC`。
- 趋势轮动仓
  先从主流山寨候选池中做月度更新趋势池，再在池内选出最强的 `Top 2` 标的进行持有。

整个系统默认每小时执行一次，但核心指标以日线趋势和风险因子为主，因此更关注中周期趋势，而不是高频噪音。

## BTC 核心仓策略

### 使用指标

- `MA200`
- `MA200 slope`
- `AHR999`
- `Z-Score`
- `动态 Z-Score 止盈阈值`

### 逻辑说明

- 当 `AHR999` 偏低时，提高 `BTC` 建仓力度。
- 当 `AHR999` 回到中性区域时，保持正常节奏建仓。
- 当 `Z-Score` 超过历史动态高估阈值时，分批卖出部分 `BTC`。
- `Z-Score` 越高，止盈比例越大。

### BTC 目标仓位

`BTC` 并不是固定占比，而是随着组合净值增长逐步提高核心权重：

```text
btc_target_ratio = 0.14 + 0.16 * ln(1 + total_equity / 10000)
```

策略内部对该比例做上下限约束，避免过低或过高。  
这意味着资金越大，组合越偏向 `BTC` 核心资产；资金较小阶段，趋势仓有更高的进攻性。

### BTC 建仓额度

每日基础定投额度也会随着总净值自动提高，而不是始终使用固定小额买入。

## 趋势轮动策略

### 候选宇宙来源

趋势轮动候选范围默认不再由本仓库手工维护，而是优先读取上游 `CryptoLeaderRotation` 发布的生产版 `live_pool_legacy.json` / Firestore 当前生效池。

当前读取优先级如下：

1. Firestore `strategy/CRYPTO_LEADER_ROTATION_LIVE_POOL`
2. 本地 `live_pool_legacy.json`
3. 本仓库内置静态 `TREND_UNIVERSE`

内置静态池仍然保留，用途是：

- 上游发布暂时不可用时的安全回退
- 本地离线调试
- 首次接入阶段的兼容保底

### 月更趋势池

上游每月会先生成一个 `5` 币生产趋势池，本项目直接消费该结果。  
这一步采用“稳健质量排序”，核心目标不是追逐最尖锐的短线强势，而是优先挑选：

- 趋势结构稳定
- 相对 `BTC` 具备中期强度
- 流动性充足
- 流动性波动较平稳
- 长期趋势持续性较好

### 稳健质量排序使用的因子

- `SMA20`
- `SMA60`
- `SMA200`
- `20 / 60 / 120 日收益`
- `20 日波动率`
- `ATR14`
- `30 / 90 / 180 日平均成交额`
- `趋势持续性`
- `相对 BTC 强弱`
- `风险调整后动量`

### 月更池排序思路

月更池构建时，综合考虑以下因子：

- 趋势质量
  价格相对 `SMA20 / SMA60 / SMA200` 的强弱程度。
- 趋势持续性
  最近一段时间维持在大趋势之上的稳定程度。
- 流动性
  使用中长期成交额衡量可交易性。
- 流动性稳定性
  避免只因为短期放量而误判。
- 相对 `BTC` 强度
  比较候选币相对 `BTC` 的阶段收益优势。
- 风险调整后动量
  用动量除以波动率，避免单纯追高高波动币。

上游策略还会对上月已入池的币给予轻微连续性加分，以减少月度频繁换池。

### 最终持仓选择

在月更趋势池内，系统进一步做池内轮动：

- 只持有 `Top 2`
- 使用相对 `BTC` 的风险调整后强弱分数做最终排序
- 权重采用**逆波动率加权**

### 入场条件

- `BTC` 趋势闸门开启
- 价格高于 `SMA20`
- 价格高于 `SMA60`
- 价格高于 `SMA200`
- 相对 `BTC` 分数为正
- 绝对动量为正

### 出场条件

- 跌破 `SMA60`
- 触发 `ATR` 移动止盈
- 被轮动模型移出最终持仓名单

## 风险控制

### BTC 趋势闸门

趋势仓只有在以下条件同时满足时才会启动：

- `BTC price > MA200`
- `MA200 slope > 0`

这样可以将大部分山寨币风险暴露限制在 `BTC` 中长期多头环境中。

### 每日熔断

若组合当日净值跌幅达到阈值：

- 立即清空趋势轮动仓
- `BTC` 核心仓保持不动
- 当日不再继续执行趋势买入

### BNB 手续费燃料

系统会单独维护一部分 `BNB` 用于手续费折扣：

- 当 `BNB` 价值低于阈值时自动补仓
- `BNB` 不参与趋势轮动
- 自动补仓逻辑与主策略解耦

## 理财账户兼容

项目兼容币安活期理财使用场景：

- 下单前自动检查现货余额
- 现货不足时自动从活期理财赎回
- 自动维护 `USDT` 现货缓冲
- 现货过多时转入理财，现货不足时从理财补回

这使得策略可以在“现货 + 理财”混合账户结构下长期运行。

## 状态持久化

项目使用 `Google Cloud Firestore` 保存运行状态，主要包括：

- 趋势仓持仓状态
- 每个趋势标的的最高价
- 每日熔断状态
- 当日 `BTC` 是否已买入或卖出
- 月更趋势池所属月份
- 当前有效趋势池名单

当上游动态趋势池发生增删标的时，本项目还会额外处理两类兼容状态：

- 新加入的标的自动补默认持仓结构
- 已被移出当前动态池但仍有历史持仓的标的会进入 retired 状态，直到完成清仓

这保证了月更换池不会因为状态缺失或旧键残留而中断实盘脚本。

## 上游趋势池接入

默认生产接入契约来自上游仓库 `CryptoLeaderRotation`，当前推荐的读取方式是：

1. 先读 Firestore 当前生效池摘要
2. Firestore 不可用时读本地同步的 `live_pool_legacy.json`
3. 两者都不可用时回退静态趋势池

### Firestore 当前生效池

默认集合和文档：

- collection: `strategy`
- document: `CRYPTO_LEADER_ROTATION_LIVE_POOL`

脚本默认从这个文档读取 `symbol_map`。如果需要覆盖默认值，可设置：

- `TREND_POOL_FIRESTORE_COLLECTION`
- `TREND_POOL_FIRESTORE_DOCUMENT`

### 本地文件回退

如果 Firestore 不可用，脚本会继续查找 `live_pool_legacy.json`。

可显式指定文件路径：

- `TREND_POOL_FILE=/abs/path/to/live_pool_legacy.json`

如果不显式指定，脚本会尝试常见本地路径，例如：

- `../CryptoLeaderRotation/data/output/live_pool_legacy.json`
- `../crypto-leader-rotation/data/output/live_pool_legacy.json`

### 文件格式

本项目兼容的 `live_pool_legacy.json` 结构如下：

```json
{
  "as_of_date": "2026-03-13",
  "pool_size": 5,
  "symbols": {
    "TRXUSDT": {"base_asset": "TRX"},
    "ETHUSDT": {"base_asset": "ETH"},
    "BCHUSDT": {"base_asset": "BCH"},
    "NEARUSDT": {"base_asset": "NEAR"},
    "LTCUSDT": {"base_asset": "LTC"}
  }
}
```

### 动态池运行规则

- 当前 active 动态池用于新的趋势候选与买入决策
- 已被移出动态池但尚未清仓的标的仍会继续参与估值、风控和卖出
- 不会因为月更切池而直接丢失旧持仓状态
- 当动态池读取失败时，策略会回退到静态池继续运行

## 环境变量

运行前请配置以下环境变量：

- `BINANCE_API_KEY`
- `BINANCE_API_SECRET`
- `TG_TOKEN`
- `TG_CHAT_ID`
- `GCP_SA_KEY`
- `BTC_STATUS_REPORT_INTERVAL_HOURS`（可选，BTC 心跳推送间隔，单位小时，默认 `24`）

如需显式指定上游趋势池来源，还可选配：

- `TREND_POOL_FILE`
- `TREND_POOL_FIRESTORE_COLLECTION`
- `TREND_POOL_FIRESTORE_DOCUMENT`

如果运行环境使用 `Google Cloud` 默认凭证，也可以按实际部署方式提供 Firestore 访问权限。

## 快速部署

### 1. 准备 Python 环境

推荐使用 `Python 3.9+`。

```bash
python3 -m venv .venv
source .venv/bin/activate
pip install -r requirements.txt
```

### 2. 配置环境变量

示例：

```bash
export BINANCE_API_KEY="your_binance_key"
export BINANCE_API_SECRET="your_binance_secret"
export TG_TOKEN="your_telegram_bot_token"
export TG_CHAT_ID="your_telegram_chat_id"
export GCP_SA_KEY="/path/to/service-account.json"
export TREND_POOL_FILE="/path/to/live_pool_legacy.json"
```

如果你使用的是本地服务账号文件，也可以额外设置：

```bash
export GOOGLE_APPLICATION_CREDENTIALS="$GCP_SA_KEY"
```

如果你准备直接读取上游发布到 Firestore 的当前生效池，还可按需设置：

```bash
export TREND_POOL_FIRESTORE_COLLECTION="strategy"
export TREND_POOL_FIRESTORE_DOCUMENT="CRYPTO_LEADER_ROTATION_LIVE_POOL"
```

### 3. 启动实盘脚本

```bash
python3 main.py
```

## 定时运行

实盘建议每小时执行一次，可以用 `cron` 或云函数调度。

### `cron` 示例

```cron
0 * * * * cd /path/to/BinanceQuant && /path/to/.venv/bin/python main.py >> run.log 2>&1
```

如果你依赖上游月更发布的动态池，建议在上游完成月更发布后继续沿用同一份 Firestore / `live_pool_legacy.json` 输出，而不是在下游额外维护一份手工候选列表。

## 回测与研究

研究脚本用于验证自动选池、排序与风险控制：

```bash
python3 backtest.py
```

回测脚本主要用于：

- 比较不同自动选池方案
- 评估强币发现能力
- 检查不同排序模型的长期收益与回撤
- 验证特定事件窗口下的组合表现

## Telegram 推送

项目支持将以下信息推送到 Telegram：

- 趋势买入与卖出
- `BTC` 定投建仓与止盈
- 理财赎回与资金调度
- 熔断触发
- 系统异常
- 周期性 `BTC` 状态心跳（包含 AHR999、Z-Score、BTC 目标仓位与趋势层日内表现）

默认心跳频率为 **每天一次（`BTC_STATUS_REPORT_INTERVAL_HOURS=24`）**，在 `UTC 00:00` 附近发送，对应北京时间早上约 `08:00`。  
如需更高频率（例如每 6 小时一次），可通过环境变量调整：

```bash
export BTC_STATUS_REPORT_INTERVAL_HOURS=6
```

## 适用场景

这个项目适合以下账户风格：

- 希望长期积累 `BTC`
- 同时利用主流山寨趋势获取超额收益
- 币安账户开启了活期理财
- 接受每小时调度、日线级别趋势决策
- 需要较强的自动化与状态持久化支持
