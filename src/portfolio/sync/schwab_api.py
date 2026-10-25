"""Money3本地同步组件；从既有账本适配器迁入并裁剪非同步入口。"""

from __future__ import annotations
from datetime import datetime, timedelta, timezone


from functools import lru_cache


import json


import math


import os


from pathlib import Path


import socket


import ssl


import time


import warnings


from uuid import uuid4


from zoneinfo import ZoneInfo


import pandas as pd


from portfolio.paths import PROJECT_DIR as BASE_DIR, API_CONFIG_PATH

API_CONFIG_FILE = API_CONFIG_PATH
CONFIG_FILE = BASE_DIR / "schwab_config.json"


ARCHIVE_FILE = BASE_DIR / "原始数据" / "嘉信" / "交易活动.json"


NY = ZoneInfo("America/New_York")


TRADE_COLUMNS = ["订单ID", "交易时间", "Ticker", "方向", "成交数量", "成交均价", "成交金额", "谁"]


class SchwabError(RuntimeError):
    """只向用户展示不含凭证的错误。"""


def load_schwab_auth():
    """加载授权模块时只屏蔽已知 httpx 兼容层弃用提示。"""
    from authlib.deprecate import AuthlibDeprecationWarning

    with warnings.catch_warnings():
        warnings.filterwarnings(
            "ignore",
            message=r"^The httpx module is deprecated; please use httpx2 instead\.$",
            category=AuthlibDeprecationWarning,
            module=r"^authlib\.integrations\.httpx_client(?:\.|$)",
        )
        from schwab import auth
    return auth


READ_STAGES = {
    "get_account_numbers": "获取嘉信授权账户列表",
    "get_account": "获取嘉信账户及持仓",
    "get_transactions": "查询嘉信交易记录",
    "get_quotes": "查询嘉信报价",
    "get_price_history_every_day": "查询嘉信历史日K",
}


def _exception_chain(exc: Exception) -> list[Exception]:
    chain = []
    current = exc
    while current is not None and len(chain) < 8 and all(current is not entry for entry in chain):
        chain.append(current)
        current = current.__cause__ or current.__context__
    return chain


def _tls_failure_kind(chain: list[Exception]) -> str | None:
    internal_text = " ".join(str(error).lower() for error in chain)
    if any(isinstance(error, ssl.SSLCertVerificationError) for error in chain) or any(
        marker in internal_text for marker in ("certificate_verify_failed", "certificate verify failed")
    ):
        return "certificate"
    if any(isinstance(error, (ssl.SSLEOFError, ssl.SSLZeroReturnError)) for error in chain) or any(
        marker in internal_text
        for marker in ("unexpected_eof_while_reading", "eof occurred in violation of protocol")
    ):
        return "interrupted"
    if any(isinstance(error, ssl.SSLError) for error in chain):
        return "handshake"
    return None


def _transient_read_failure(exc: Exception) -> bool:
    chain = _exception_chain(exc)
    # 证书/协议配置及 OAuth 失败不能用重试掩盖；不降低 HTTPS 校验要求。
    kind = _tls_failure_kind(chain)
    if kind in {"certificate", "handshake"} or any(
        isinstance(error, SchwabError) or getattr(error, "error", None) for error in chain
    ):
        return False
    if kind == "interrupted":
        return True
    classes = {base.__name__ for error in chain for base in type(error).__mro__}
    return bool(
        classes & {"ConnectError", "ReadError", "RemoteProtocolError", "ConnectTimeout", "ReadTimeout"}
    )


def http_failure(status: int, stage: str) -> str:
    reasons = {
        400: "请求参数被拒绝。请检查查询日期范围及应用配置。",
        401: "服务器拒绝身份验证。请运行 Money3的 scripts/authorize_schwab.py 重新授权。",
        403: "服务器拒绝访问。请检查应用是否 Ready For Use、是否开通对应 API 产品，以及授权时是否勾选该账户。",
        404: "接口或指定账户不存在。请检查授权账户与应用环境。",
        429: "请求过于频繁。请稍后重试，避免连续反复启动脚本。",
    }
    reason = reasons.get(
        status,
        "嘉信服务暂时异常，请稍后重试。" if status >= 500 else "请求未成功，请检查应用权限及账户授权。",
    )
    return f"{stage}失败 [HTTP {status}]：{reason}"


def describe_exception(exc: Exception, stage: str) -> str:
    """只展示已知原因和异常类名；不输出响应正文、请求 URL 或原始异常文字。"""
    if isinstance(exc, SchwabError):
        return str(exc)
    chain = _exception_chain(exc)
    # 嘉信刷新失败的 error 字段不总是标准 OAuth 枚举；只在 OAuth
    # 异常中识别固定描述，不展示可能带凭证的原始服务端文字。
    for error in chain:
        if not any(base.__name__ == "OAuthError" for base in type(error).__mro__):
            continue
        description = str(getattr(error, "description", "")).lower()
        if "refresh token" in description and any(
            marker in description for marker in ("expired", "revoked", "invalid")
        ):
            return (
                f"{stage}失败 [嘉信刷新授权失效]：嘉信拒绝刷新 Token（已过期、撤销或无效）。"
                "刷新授权通常约 7 天需要重新登录，反复重试不能恢复。请运行 "
                "money3/.venv/bin/python money3/scripts/authorize_schwab.py，在本机浏览器重新授权后再运行Money3同步。"
                "不要把 Token 或完整回调网址发送给他人。"
            )
    oauth_reasons = {
        "invalid_grant": "Refresh Token 已过期、撤销或不可用；若发生在首次授权，授权码也可能过期或已使用。请重新运行 --authorize，使用本次新链接完成授权。",
        "invalid_client": "嘉信拒绝客户端认证或刷新授权；约 7 天的刷新 Token 失效也可能返回此错误。请先运行 --authorize 重新登录；若新授权仍失败，再核对 app_key 和 app_secret 是否来自同一个已就绪应用。",
        "unauthorized_client": "应用未获准使用当前授权方式。请检查应用状态及 API 产品权限。",
        "invalid_scope": "请求的授权范围未获批准。请检查应用开通的 API 产品。",
        "access_denied": "账户授权被拒绝或撤销。请重新授权并勾选要查询的账户。",
        "mismatching_state": "回调网址与本次授权会话不匹配。请使用当前运行生成的新链接授权，并把完整回调网址粘回同一终端。",
        "missing_code": "回调网址缺少授权码。请复制授权完成后浏览器地址栏的完整网址。",
    }
    for error in chain:
        code = getattr(error, "error", None)
        if isinstance(code, str) and code in oauth_reasons:
            return f"{stage}失败 [OAuth {code}]：{oauth_reasons[code]}"
    if any(isinstance(error, PermissionError) for error in chain):
        return f"{stage}失败 [文件权限]：无法读取或保存本地授权文件，请检查 Token 文件及所在目录的读写权限。"
    if "加载嘉信 Token" in stage and any(
        isinstance(error, (json.JSONDecodeError, KeyError)) for error in chain
    ):
        return f"{stage}失败 [Token 格式]：授权文件不是完整的 SDK Token 格式，请重新导入 Token 或运行 --authorize。"
    if any(isinstance(error, ImportError) for error in chain):
        return f"{stage}失败 [缺少依赖]：请使用项目 .venv 解释器，并安装 money3的sync可选依赖 中的依赖。"
    # 仅在内部识别固定错误标记，绝不把可能包含密钥的异常原文展示出来。
    internal_text = " ".join(str(error).lower() for error in chain)
    tls_kind = _tls_failure_kind(chain)
    if tls_kind == "certificate":
        code = next(
            (
                getattr(error, "verify_code", None)
                for error in chain
                if isinstance(getattr(error, "verify_code", None), int)
            ),
            None,
        )
        reason = {
            9: "证书尚未生效",
            10: "证书已过期",
            18: "收到未受信任的自签名证书",
            19: "证书链包含未受信任的自签名证书",
            20: "找不到受信任的签发者",
            21: "无法验证完整证书链",
            62: "证书域名与访问地址不匹配",
        }.get(code, "证书校验未通过")
        detail = f"（校验码 {code}）" if code is not None else ""
        return f"{stage}失败 [HTTPS 证书]：{reason}{detail}。请检查系统时间及代理/企业网络证书配置。"
    if tls_kind == "interrupted":
        return f"{stage}失败 [TLS 连接中断]：HTTPS 连接被对端或网络链路提前关闭（EOF）；这不等同于证书过期或 Token 失效。请检查网络/代理连接稳定性。"
    if tls_kind == "handshake":
        return f"{stage}失败 [TLS 握手]：TLS 协议协商失败。请检查网络/代理与 TLS 配置；当前异常并非证书校验失败。"
    if any(isinstance(error, socket.gaierror) for error in chain) or any(
        marker in internal_text
        for marker in ("nodename nor servname", "name or service not known", "getaddrinfo failed")
    ):
        return f"{stage}失败 [DNS]：无法解析 api.schwabapi.com。请检查网络、DNS 和代理配置。"
    # 按类继承名称判断，无需为错误提示额外导入 SDK 的 HTTP 依赖。
    classes = {base.__name__ for error in chain for base in type(error).__mro__}
    for kind, label in (
        ("ConnectTimeout", "连接超时"),
        ("ReadTimeout", "读取响应超时"),
        ("WriteTimeout", "发送请求超时"),
        ("PoolTimeout", "连接池等待超时"),
    ):
        if kind in classes:
            return f"{stage}失败 [{label}]：请求在设定的等待时间内未完成。请检查到 api.schwabapi.com 的网络/代理后重试；超时本身不代表 Token 过期。"
    if "TimeoutException" in classes or any(isinstance(error, TimeoutError) for error in chain):
        return f"{stage}失败 [超时]：等待嘉信响应超时，请检查网络后重试。"
    if "ProxyError" in classes:
        return f"{stage}失败 [代理连接]：无法通过当前代理连接嘉信。请检查系统及终端代理设置。"
    if "ConnectError" in classes or "ConnectionError" in classes:
        return f"{stage}失败 [网络连接]：未能连接嘉信服务器。请检查网络及代理后重试。"
    if classes & {"ReadError", "WriteError", "RemoteProtocolError"}:
        return f"{stage}失败 [连接中断]：与嘉信通信时连接被中断，请检查网络/代理后重试。"
    response = getattr(exc, "response", None)
    status = getattr(response, "status_code", None)
    if isinstance(status, int):
        return http_failure(status, stage)
    return f"{stage}失败 [SDK {type(exc).__name__}]：未识别的 SDK 异常。请反馈此阶段和异常类型；不要发送密钥、Token 或完整授权网址。"


def atomic_json(path: Path, value: dict, *, private: bool = False) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.{uuid4().hex}.tmp")
    try:
        with temporary.open("x", encoding="utf-8") as stream:
            if private:
                os.chmod(temporary, 0o600)
            json.dump(value, stream, ensure_ascii=False, indent=2)
        os.replace(temporary, path)
    finally:
        temporary.unlink(missing_ok=True)


def load_config(*, require_enabled: bool = True) -> dict | None:
    config = None
    if API_CONFIG_FILE.is_file():
        try:
            raw = json.loads(API_CONFIG_FILE.read_text(encoding="utf-8"))
            if isinstance(raw, dict) and "schwab" in raw and isinstance(raw["schwab"], dict):
                config = raw["schwab"]
        except (ValueError, OSError):
            raise SchwabError("无法读取 api_config.json，请检查 JSON 格式。") from None

    if config is None:
        if not CONFIG_FILE.exists():
            return None
        try:
            config = json.loads(CONFIG_FILE.read_text(encoding="utf-8"))
        except (ValueError, OSError):
            raise SchwabError("无法读取 schwab_config.json，请检查 JSON 格式。") from None
    if not isinstance(config, dict) or not isinstance(config.get("enabled"), bool):
        raise SchwabError("嘉信配置 enabled 必须为 true 或 false。")
    if require_enabled and not config["enabled"]:
        return None
    for key in ("app_key", "app_secret", "callback_url", "token_file", "history_start"):
        if not isinstance(config.get(key), str) or not config[key].strip():
            raise SchwabError(f"嘉信配置缺少字段：{key}。请在本地配置中填写。")
    try:
        start = datetime.strptime(config["history_start"], "%Y-%m-%d").replace(tzinfo=NY)
    except ValueError:
        raise SchwabError("嘉信 history_start 必须为 YYYY-MM-DD。") from None
    if start.date() < datetime(2026, 7, 1).date():
        raise SchwabError("当前标准流水从 2026-07 起拆分；更早的嘉信历史需先接入历史底稿。")
    if start > datetime.now(NY):
        raise SchwabError("嘉信 history_start 不能晚于今天。")
    if config.get("history_price_basis", "verify") not in {"verify", "raw", "split_adjusted"}:
        raise SchwabError("嘉信 history_price_basis 只支持 verify、raw 或 split_adjusted。")
    return config


def token_path(config: dict) -> Path:
    path = Path(config["token_file"]).expanduser()
    return path if path.is_absolute() else BASE_DIR / path


@lru_cache(maxsize=1)
def get_service() -> SchwabService | None:
    config = load_config()
    return SchwabService(config) if config else None


def response_json(response, label: str):
    if response.status_code != 200:
        raise SchwabError(http_failure(response.status_code, label))
    try:
        return response.json()
    except ValueError:
        raise SchwabError(f"嘉信{label}返回无效 JSON。") from None


def number(value) -> float:
    try:
        result = float(value)
    except (TypeError, ValueError):
        raise SchwabError("嘉信响应缺少有效金额、价格或股数，已停止计算。") from None
    if not math.isfinite(result):
        raise SchwabError("嘉信响应包含非有限数值。")
    return result


def equity_symbol(instrument: dict) -> str:
    is_etf = (
        instrument.get("assetType") == "COLLECTIVE_INVESTMENT"
        and instrument.get("type") == "EXCHANGE_TRADED_FUND"
    )
    if instrument.get("assetType") != "EQUITY" and not is_etf:
        raise SchwabError("当前收益引擎仅支持股票/ETF；嘉信账户存在其他证券，需单独适配。")
    symbol = str(instrument.get("symbol", "")).strip().upper()
    # 现有核心代码会截断带点股票代码；禁止把不同类别股票错误合并。
    if not symbol or not all(c.isalpha() or c == "-" for c in symbol):
        raise SchwabError("嘉信存在当前代码无法安全归一化的股票代码，需先适配代码映射。")
    return {"SKHYV": "SKHY"}.get(symbol, symbol)


def normalize_transactions(transactions: list[dict]) -> tuple[pd.DataFrame, pd.DataFrame]:
    """成交使用实际净收付额，分红/利息和资金转账独立留档。"""
    trades, cash, seen = [], [], set()
    for activity in transactions:
        identity = str(activity.get("activityId", "")).strip()
        if not identity or identity in seen:
            raise SchwabError("嘉信交易活动 ID 缺失或重复，无法安全去重。")
        seen.add(identity)
        when = pd.to_datetime(activity.get("time") or activity.get("tradeDate"), utc=True, errors="coerce")
        if pd.isna(when):
            raise SchwabError("嘉信交易活动缺少有效日期。")
        when = when.tz_convert(NY).tz_localize(None)
        if activity.get("tradeDate"):
            # tradeDate 是券商给出的交易归属日；纯日期不能先当 UTC 再减成前一天。
            day = pd.to_datetime(str(activity["tradeDate"])[:10], errors="coerce")
            if pd.isna(day):
                raise SchwabError("嘉信成交归属日期无效。")
            when = day.normalize() + (when - when.normalize())
        status = str(activity.get("status", "VALID")).upper()
        if status in {"CANCELED", "CANCELLED", "REVERSED"}:
            continue
        if status not in {"VALID", "POSTED", "SETTLED"}:
            raise SchwabError("嘉信存在待确认的交易状态，已停止覆盖流水。")
        kind = activity.get("type")
        items = activity.get("transferItems") or []
        securities = [
            i for i in items if (i.get("instrument") or {}).get("assetType") not in {None, "CURRENCY"}
        ]
        if kind != "TRADE":
            if securities and kind != "DIVIDEND_OR_INTEREST":
                raise SchwabError("嘉信存在证券划转/公司行动，需补齐成本及拆并股规则后再计算。")
            cash.append(
                {
                    "活动ID": identity,
                    "日期": when,
                    "渠道": "SCHWAB",
                    "类型": kind,
                    "净金额(USD)": number(activity.get("netAmount")),
                    "说明": str(activity.get("description", "")),
                }
            )
            continue
        if len(securities) != 1:
            raise SchwabError("嘉信成交包含多证券或缺少证券明细，不能自动分配成本。")
        item = securities[0]
        symbol = equity_symbol(item["instrument"])
        quantity = number(item.get("amount"))
        price = number(item.get("price"))
        net = number(activity.get("netAmount"))
        if quantity == 0 or price <= 0 or net == 0 or quantity * net >= 0:
            raise SchwabError("嘉信成交数量与净金额方向不符，需核对成交/红利再投资。")
        trades.append(
            {
                "订单ID": f"SCHWAB:{identity}",
                "交易时间": when,
                "Ticker": symbol,
                "方向": "买入" if quantity > 0 else "卖出",
                "成交数量": abs(quantity),
                "成交均价": price,
                "成交金额": abs(net),
                "谁": "",
            }
        )
    return pd.DataFrame(trades, columns=TRADE_COLUMNS), pd.DataFrame(
        cash, columns=["活动ID", "日期", "渠道", "类型", "净金额(USD)", "说明"]
    )


def live_positions(account: dict) -> dict[str, float]:
    if not isinstance(account, dict) or "securitiesAccount" not in account:
        raise SchwabError("嘉信账户响应缺少 securitiesAccount。")
    body = account["securitiesAccount"]
    if "positions" not in body:
        # 空仓允许空数组；缺失字段不能证明空仓。
        raise SchwabError("嘉信账户响应缺少 positions，不能当作零持仓。")
    output: dict[str, float] = {}
    for position in body["positions"]:
        long_qty, short_qty = (
            number(position.get("longQuantity", 0)),
            number(position.get("shortQuantity", 0)),
        )
        if short_qty != 0:
            raise SchwabError("当前收益引擎不支持嘉信空头持仓。")
        if long_qty:
            symbol = equity_symbol(position.get("instrument") or {})
            output[symbol] = output.get(symbol, 0.0) + long_qty
    return output


class SchwabService:
    def __init__(self, config: dict):
        self.config = config
        self._client = None
        self._account_hash = None

    @property
    def client(self):
        if self._client is None:
            path = token_path(self.config)
            if not path.is_file():
                raise SchwabError("缺少嘉信 Token，请先运行 Money3的 scripts/authorize_schwab.py。")
            try:
                self._client = load_schwab_auth().client_from_token_file(
                    str(path), self.config["app_key"], self.config["app_secret"]
                )
                self._client.set_timeout(30)
            except ImportError:
                raise SchwabError("缺少 schwab-py，请安装 money3的sync可选依赖。") from None
            except Exception as exc:
                raise SchwabError(describe_exception(exc, "加载嘉信 Token / 初始化 SDK")) from None
        return self._client

    def read(self, method, *args, **kwargs):
        name = getattr(method, "__name__", "")
        stage = READ_STAGES.get(name, "调用嘉信接口")
        attempts = 3 if name in READ_STAGES else 1
        for attempt in range(1, attempts + 1):
            try:
                return response_json(method(*args, **kwargs), stage)
            except SchwabError:
                raise
            except Exception as exc:
                message = describe_exception(exc, stage)
                if not _transient_read_failure(exc):
                    raise SchwabError(message) from None
                if attempt == attempts:
                    suffix = f"（已尝试 {attempt} 次，仍未恢复。）" if attempt > 1 else ""
                    raise SchwabError(message + suffix) from None
                print(f"[RETRY] {message} {attempt} 秒后重试（第 {attempt + 1}/{attempts} 次）。", flush=True)
                time.sleep(attempt)

    @property
    def account_hash(self) -> str:
        if self._account_hash is None:
            accounts = self.read(self.client.get_account_numbers)
            if not isinstance(accounts, list):
                raise SchwabError("嘉信账户列表响应格式不正确。")
            requested = str(self.config.get("account_number", "")).strip()
            matches = [a for a in accounts if not requested or str(a.get("accountNumber")) == requested]
            if len(matches) != 1 or not matches[0].get("hashValue"):
                raise SchwabError(
                    "请在 account_number 配置中选择一个已授权嘉信账户；本次不自动合并多个账户。"
                )
            self._account_hash = str(matches[0]["hashValue"])
        return self._account_hash

    def account(self) -> dict:
        return self.read(
            self.client.get_account, self.account_hash, fields=self.client.Account.Fields.POSITIONS
        )

    def history(self, *, archived: dict | None = None, use_local_archive: bool = True) -> dict:
        """首次自起点拉取，之后回溯三天；完整历史持久留档，不只保留 API 查询窗口。"""
        beginning = datetime.strptime(self.config["history_start"], "%Y-%m-%d").replace(tzinfo=NY)
        if archived is None:
            archived = (
                json.loads(ARCHIVE_FILE.read_text(encoding="utf-8"))
                if use_local_archive and ARCHIVE_FILE.exists()
                else {}
            )
        if archived and (
            archived.get("account_hash") != self.account_hash
            or archived.get("history_start") != self.config["history_start"]
        ):
            raise SchwabError("嘉信历史档案与当前账户/起点不同，请先核对档案，不能混合两个账户。")
        start = beginning
        if archived.get("fetched_through"):
            start = max(beginning, datetime.fromisoformat(archived["fetched_through"]) - timedelta(days=3))
        end = datetime.now(timezone.utc)
        records = {str(r["activityId"]): r for r in archived.get("transactions", [])}
        while start < end:
            window_end = min(start + timedelta(days=30), end)
            batch = self.read(
                self.client.get_transactions,
                self.account_hash,
                start_date=start,
                end_date=window_end,
                transaction_types=list(self.client.Transactions.TransactionType),
            )
            if not isinstance(batch, list) or len(batch) >= 3000:
                raise SchwabError("嘉信历史查询结果异常或可能被截断，已停止计算。")
            for record in batch:
                if not record.get("activityId"):
                    raise SchwabError("嘉信返回缺少活动 ID 的历史记录。")
                records[str(record["activityId"])] = record
            start = window_end
        return {
            "account_hash": self.account_hash,
            "history_start": self.config["history_start"],
            "fetched_through": end.isoformat(),
            "transactions": list(records.values()),
        }
