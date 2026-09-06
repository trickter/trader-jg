# 热门币雷达 V2

从 Binance Alpha 1H、OKX Trending 4H 和 GMGN Trending 6H 发现 BSC、Solana、Robinhood 热门币，按主池流动性和活跃度过滤。V2 增加高市值代币回调研究：市值资格与价格回调分开计算，用小时 K 线回放 2/3、1/3、1/6 三档，比较触碰入场和确认入场。程序只读行情，不包含钱包和交易能力。

## 快速启动

Windows PowerShell：

```powershell
Copy-Item .env.example .env
./scripts/start.ps1
```

Linux/macOS：

```bash
cp .env.example .env
chmod +x scripts/start.sh
./scripts/start.sh
```

浏览器打开 `http://127.0.0.1:8000`；回调研究页是 `http://127.0.0.1:8000/research`。首次启动会创建虚拟环境并安装依赖。没有 OKX 凭据时，Binance 和 GMGN 仍会运行，健康页会显示 OKX 未配置。

GMGN 热榜使用官方 OpenAPI 的 6H 周期（官方没有 4H 热榜周期）。未设置 `GMGN_API_KEY` 时使用官方公开只读 Key；长期运行建议在 `.env` 中配置个人 Key。DexScreener 仅保留为价格、主池流动性、市值和成交数据的行情补全源，不再参与热榜发现；查不到交易对时，以 GMGN 自带的价格、市值和流动性兜底。

## OKX 配置

在 `.env` 或系统环境中提供：

```text
OKX_API_KEY=
OKX_SECRET_KEY=
OKX_API_PASSPHRASE=
# 可选：只有 OKX 明确要求时才填写
OKX_PROJECT_ID=
```

启动脚本会导入 `.env`；使用 systemd 时由 `EnvironmentFile` 加载。直接运行 `python -m radar` 前应由 shell 导入这些变量。凭据不会进入数据库、网页或日志。

## 分开运行

```bash
python -m venv .venv
python -m pip install -e ".[test]"
python -m radar init-db
python -m radar collector
python -m radar serve
```

单次验证采集使用 `python -m radar collect-once`。采集器与网页进程彼此独立，数据存储在 `data/radar.db`。

## 回调研究流程

先持续采集，让同一代币留下可核验的价格与市值采样；然后执行：

```bash
python -m radar research-import
python -m radar backfill --days 90 --limit 20
python -m radar replay
```

也可用 `python -m radar research-worker --days 90 --limit 20` 顺序完成三步。`research-import` 会把曾达到 3000 万美元市值且市值/价格隐含供应量稳定的代币标为 `VERIFIED`；证据不足或供应量跳变的代币保留为 `PENDING`，代币化股票标为 `EXCLUDED`。只有 `VERIFIED` 会回填 GeckoTerminal 小时线并进入回放。

回放在每根 K 线当时只使用此前最多 720 小时数据计算价格前高，避免未来数据泄漏。触碰策略在跌破层级后的下一根 K 线开盘入场；确认策略要求 24 小时内收盘突破此前 3 根 K 线高点，再于下一根开盘入场。目标和止损均按净收益 ±25% 计算，另计 1% 往返成本，最长持有 168 小时。同一小时同时触及两条边界时标为 `AMBIGUOUS`，不计入胜率。

## 规则和接口

编辑 `config/rules.yaml` 后，下一个行情周期自动加载新版本。每项规则可设为 `reject`、`warn` 或 `disabled`，链级覆盖写在 `chain_overrides`。每次判断关联配置哈希，可重放历史判断。

- `GET /api/candidates`：候选列表和筛选
- `GET /api/candidates/{id}`：来源、判断、主池切换
- `GET /api/candidates/{id}/snapshots`：采样历史
- `GET /api/health/sources`：来源健康与调用计数
- `GET /api/report?days=7`：七日采集和过滤分布
- `GET /api/export.csv`：CSV 导出
- `GET /api/strategy/candidates`：研究资格、回调层级与最新回放结果
- `GET /api/strategy/candidates/{id}/candles`：已校验的小时 K 线
- `GET /api/strategy/runs/{id}/report`：按入场方式、层级和结果聚合的回放报告
- `GET /api/strategy/health`：资格、K 线回填任务和最新回放状态

## 常驻运行

Linux 将项目放到 `/opt/hot-coin-radar`，建立虚拟环境和 `.env`，复制 `deploy/*.service` 到 `/etc/systemd/system/` 后启用两个服务。

Windows 可在“任务计划程序”创建两个“登录时”任务，程序分别为 `.venv\Scripts\python.exe`，参数分别是 `-m radar collector` 和 `-m radar serve`，起始目录设置为项目目录。不要同时运行 `start.ps1` 和计划任务中的采集器，以免重复请求。

## 数据说明

主池只从目标 Token 位于 base 侧的有效池中选择，以流动性最高者为准。K 线回填再次核对 GeckoTerminal 返回的 base token 合约，合约不一致时整批拒绝。OKX 4H 与 GMGN 6H 聚合指标按来源和周期分开保存，不再写入 1H 字段；过期聚合数据不会伪装成当前快照。空字符串保持为空，百分比 0 是有效值，风险等级 0 视为未定义；主池创建、OKX 首次交易和系统首次发现是三个独立时间。
