# 热门币雷达 V1

从 Binance Alpha 1H、OKX Trending 4H 和 GMGN Trending 6H 发现 BSC、Solana、Robinhood 热门币，按主池流动性和活跃度过滤，并在掉榜后继续观察 72 小时。程序只读行情，不包含钱包和交易能力。

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

浏览器打开 `http://127.0.0.1:8000`。首次启动会创建虚拟环境并安装依赖。没有 OKX 凭据时，Binance 和 GMGN 仍会运行，健康页会显示 OKX 未配置。

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

## 规则和接口

编辑 `config/rules.yaml` 后，下一个行情周期自动加载新版本。每项规则可设为 `reject`、`warn` 或 `disabled`，链级覆盖写在 `chain_overrides`。每次判断关联配置哈希，可重放历史判断。

- `GET /api/candidates`：候选列表和筛选
- `GET /api/candidates/{id}`：来源、判断、主池切换
- `GET /api/candidates/{id}/snapshots`：采样历史
- `GET /api/health/sources`：来源健康与调用计数
- `GET /api/report?days=7`：七日采集和过滤分布
- `GET /api/export.csv`：CSV 导出

## 常驻运行

Linux 将项目放到 `/opt/hot-coin-radar`，建立虚拟环境和 `.env`，复制 `deploy/*.service` 到 `/etc/systemd/system/` 后启用两个服务。

Windows 可在“任务计划程序”创建两个“登录时”任务，程序分别为 `.venv\Scripts\python.exe`，参数分别是 `-m radar collector` 和 `-m radar serve`，起始目录设置为项目目录。不要同时运行 `start.ps1` 和计划任务中的采集器，以免重复请求。

## 数据说明

主池只从目标 Token 位于 base 侧的有效池中选择，以流动性最高者为准。OKX 的 Token 聚合指标与 DEX 主池数据分开保存。空字符串保持为空，风险等级 0 视为未定义；主池创建、OKX 首次交易和系统首次发现是三个独立时间。
