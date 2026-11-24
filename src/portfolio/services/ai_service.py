"""Codex CLI AI Service.

Bridges the local Codex CLI subscription to provide AI investment assistant capabilities
without requiring manual API key configuration or extra token billing.
Supports multi-dimensional contextual filtering across Person, Channel, and Ticker.
"""
from datetime import datetime
from decimal import Decimal
import json
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

from portfolio.paths import API_CONFIG_PATH, quote_cache_path


SUPPORTED_AI_PROVIDERS = ("codex", "gemini", "google_api")
DEFAULT_GOOGLE_API_MODEL = "gemini-2.5-flash"
DEFAULT_GOOGLE_API_BUDGET_USD = Decimal("8.50")  # 为 $10/月额度预留 $1.50 安全缓冲
DEFAULT_GOOGLE_API_MAX_OUTPUT_TOKENS = 4096
GOOGLE_API_MODELS = (
    "gemini-2.5-flash",
    "gemini-2.5-pro",
    "gemini-2.5-flash-lite",
    "gemini-3-flash-preview",
    "gemini-3.1-pro-preview",
)
# 保守估算单价（美元 / 百万 tokens）：(输入单价, 输出单价)，宁可略高估以防超额
GOOGLE_API_PRICING_USD_PER_M: dict[str, tuple[Decimal, Decimal]] = {
    "gemini-2.5-flash": (Decimal("0.30"), Decimal("2.50")),
    "gemini-2.5-flash-lite": (Decimal("0.10"), Decimal("0.40")),
    "gemini-2.5-pro": (Decimal("2.50"), Decimal("15.00")),
    "gemini-3-flash-preview": (Decimal("0.50"), Decimal("3.00")),
    "gemini-3.1-pro-preview": (Decimal("2.50"), Decimal("15.00")),
}
_DEFAULT_CONSERVATIVE_PRICING = (Decimal("2.50"), Decimal("15.00"))
_GOOGLE_API_BASE_URL = "https://generativelanguage.googleapis.com/v1beta/models"
_USAGE_LOCK = threading.Lock()


def normalize_ai_provider(provider: str | None) -> str:
    """规范化 AI 引擎名称，未知值默认回退为 codex。"""
    val = (provider or "codex").strip().lower()
    return val if val in SUPPORTED_AI_PROVIDERS else "codex"


def google_api_config_path() -> Path:
    """返回本地 Google API 配置文件路径（支持环境变量覆盖）。"""
    override = os.environ.get("MONEY3_GOOGLE_API_CONFIG", "").strip()
    if override:
        return Path(override).expanduser()
    return quote_cache_path().parent / "google_api_config.json"


def google_api_usage_path() -> Path:
    """返回本地 Google API 月度用量统计文件路径（支持环境变量覆盖）。"""
    override = os.environ.get("MONEY3_GOOGLE_API_USAGE", "").strip()
    if override:
        return Path(override).expanduser()
    return google_api_config_path().with_name("google_api_usage.json")


def current_usage_month() -> str:
    """返回当前统计月份键（YYYY-MM）。"""
    return datetime.now().strftime("%Y-%m")


def _mask_api_key(api_key: str) -> str:
    key = (api_key or "").strip()
    if len(key) <= 8:
        return "*" * len(key)
    return f"{key[:4]}...{key[-4:]}"


def _fmt(val, prefix="", suffix="", signed=False, as_percent=False, default="—") -> str:
    """安全格式化数字，遇 None 或异常返回默认占位符，绝不抛出 TypeError"""
    if val is None or val == "":
        return default
    try:
        if isinstance(val, (Decimal, int, float)):
            num = Decimal(str(val))
            if as_percent:
                num = num * 100
                suffix = "%"
            sign_str = "+" if signed else ""
            return f"{prefix}{num:{sign_str},.2f}{suffix}"
        return str(val)
    except Exception:
        return str(val) if val is not None else default


class CodexAIService:
    """本地 Codex CLI 驱动的 AI 投顾服务"""

    @staticmethod
    def get_cli_path() -> str | None:
        """获取本地 codex 可执行文件的完整绝对路径"""
        found = shutil.which("codex")
        if found:
            return found
        for candidate in (
            Path.home() / ".local/bin/codex",
            Path("/opt/homebrew/bin/codex"),
            Path("/usr/local/bin/codex"),
        ):
            if candidate.exists() and os.access(candidate, os.X_OK):
                return str(candidate)
        return None

    @staticmethod
    def get_gemini_cli_path() -> str | None:
        """获取本地 gemini-cli 可执行文件的完整绝对路径"""
        found = shutil.which("gemini")
        if found:
            return found
        candidates = [
            Path("/opt/homebrew/bin/gemini"),
            Path("/usr/local/bin/gemini"),
            Path.home() / ".local/bin/gemini",
            Path.home() / ".npm-global/bin/gemini",
        ]
        nvm_dir = Path.home() / ".nvm/versions/node"
        if nvm_dir.is_dir():
            for node_ver in sorted(nvm_dir.iterdir(), reverse=True):
                candidates.append(node_ver / "bin/gemini")
        for candidate in candidates:
            if candidate.exists() and os.access(candidate, os.X_OK):
                return str(candidate)
        return None

    @staticmethod
    def get_agy_cli_path() -> str | None:
        """获取本地 Antigravity CLI (agy) 可执行文件的完整绝对路径"""
        found = shutil.which("agy")
        if found:
            return found
        for candidate in (
            Path.home() / ".local/bin/agy",
            Path("/opt/homebrew/bin/agy"),
            Path("/usr/local/bin/agy"),
            Path.home() / ".gemini/antigravity-cli/agy",
        ):
            if candidate.exists() and os.access(candidate, os.X_OK):
                return str(candidate)
        return None

    _login_status_cache: dict[str, dict] = {}
    _login_status_timestamp: dict[str, float] = {}

    @staticmethod
    def _read_active_google_account(gemini_dir: Path) -> str:
        accounts_file = gemini_dir / "google_accounts.json"
        if accounts_file.exists():
            try:
                acct_data = json.loads(accounts_file.read_text(encoding="utf-8"))
                active = (acct_data.get("active") or "").strip()
                if active:
                    return active
            except Exception:
                pass
        return ""

    @classmethod
    def _check_gemini_login_status(cls) -> dict:
        agy_cli = cls.get_agy_cli_path()
        gemini_cli = cls.get_gemini_cli_path()
        gemini_dir = Path.home() / ".gemini"
        oauth_file = gemini_dir / "oauth_creds.json"
        has_env_key = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
        has_oauth = oauth_file.exists() and oauth_file.stat().st_size > 2
        has_agy_state = (gemini_dir / "antigravity").is_dir() or has_oauth
        active_account = cls._read_active_google_account(gemini_dir)

        # Google 已将个人账户从旧版 gemini-cli 迁移至 Antigravity CLI (agy)；仅在显式使用 API Key 时优先走 gemini-cli
        if gemini_cli and has_env_key:
            return {
                "installed": True,
                "logged_in": True,
                "account_type": "Gemini API Key",
                "message": "Gemini CLI 已就绪 (API Key)",
            }

        if agy_cli and (has_agy_state or active_account):
            acct_label = f"{active_account} · Antigravity" if active_account else "Antigravity"
            return {
                "installed": True,
                "logged_in": True,
                "account_type": acct_label,
                "message": f"Gemini (Antigravity CLI) 已就绪 ({active_account or '已认证'})",
            }

        if agy_cli:
            return {
                "installed": True,
                "logged_in": False,
                "account_type": "",
                "message": "未检测到 Google 登录凭证，请在终端或 Antigravity 中完成账号登录",
            }

        if gemini_cli:
            return {
                "installed": True,
                "logged_in": False,
                "account_type": "",
                "message": "Google 个人账号已不再支持旧版 gemini-cli，请安装 agy 或配置 GEMINI_API_KEY",
            }

        return {
            "installed": False,
            "logged_in": False,
            "account_type": "",
            "message": "未检测到 Gemini / Antigravity CLI，请安装 agy 或配置 GEMINI_API_KEY",
        }

    @staticmethod
    def _parse_positive_decimal(raw, default: Decimal) -> Decimal:
        try:
            val = Decimal(str(raw))
            return val if val >= Decimal("0") else default
        except Exception:
            return default

    @staticmethod
    def _parse_max_output_tokens(raw) -> int:
        try:
            val = int(raw)
            return max(256, min(val, 65536))
        except Exception:
            return DEFAULT_GOOGLE_API_MAX_OUTPUT_TOKENS

    @classmethod
    def get_google_api_config(cls) -> dict:
        """读取本地 Google API 配置（本地配置文件 > api_config.json > 环境变量）。"""
        model = DEFAULT_GOOGLE_API_MODEL
        budget = DEFAULT_GOOGLE_API_BUDGET_USD
        max_output_tokens = DEFAULT_GOOGLE_API_MAX_OUTPUT_TOKENS

        cfg_path = google_api_config_path()
        if cfg_path.is_file():
            try:
                data = json.loads(cfg_path.read_text(encoding="utf-8"))
                if isinstance(data, dict):
                    saved_model = str(data.get("model") or "").strip()
                    if saved_model:
                        model = saved_model
                    if data.get("monthly_budget_usd") is not None:
                        budget = cls._parse_positive_decimal(data.get("monthly_budget_usd"), budget)
                    if data.get("max_output_tokens") is not None:
                        max_output_tokens = cls._parse_max_output_tokens(data.get("max_output_tokens"))
                    saved_key = str(data.get("api_key") or "").strip()
                    if saved_key:
                        return {
                            "api_key": saved_key,
                            "model": model,
                            "monthly_budget_usd": str(budget),
                            "max_output_tokens": max_output_tokens,
                            "source": "本地配置",
                        }
            except Exception:
                pass

        if API_CONFIG_PATH.is_file():
            try:
                root_cfg = json.loads(API_CONFIG_PATH.read_text(encoding="utf-8"))
                if isinstance(root_cfg, dict):
                    root_key = str(
                        root_cfg.get("google_api_key")
                        or root_cfg.get("gemini_api_key")
                        or ""
                    ).strip()
                    root_model = str(root_cfg.get("google_api_model") or "").strip()
                    if root_model:
                        model = root_model
                    if root_cfg.get("google_api_monthly_budget_usd") is not None:
                        budget = cls._parse_positive_decimal(
                            root_cfg.get("google_api_monthly_budget_usd"), budget
                        )
                    if root_key:
                        return {
                            "api_key": root_key,
                            "model": model,
                            "monthly_budget_usd": str(budget),
                            "max_output_tokens": max_output_tokens,
                            "source": "api_config.json",
                        }
            except Exception:
                pass

        for env_name in ("GEMINI_API_KEY", "GOOGLE_API_KEY"):
            env_key = os.environ.get(env_name, "").strip()
            if env_key:
                return {
                    "api_key": env_key,
                    "model": model,
                    "monthly_budget_usd": str(budget),
                    "max_output_tokens": max_output_tokens,
                    "source": f"环境变量 {env_name}",
                }

        return {
            "api_key": "",
            "model": model,
            "monthly_budget_usd": str(budget),
            "max_output_tokens": max_output_tokens,
            "source": "",
        }

    @classmethod
    def save_google_api_config(
        cls,
        api_key: str,
        model: str | None = None,
        monthly_budget_usd: Decimal | str | float | None = None,
        max_output_tokens: int | None = None,
    ) -> Path:
        """持久化 Google API Key、默认模型与月度安全预算上限到本地目录（权限 0600）。"""
        cfg_path = google_api_config_path()
        cfg_path.parent.mkdir(parents=True, exist_ok=True)
        existing = cls.get_google_api_config()
        clean_key = (api_key or "").strip()
        clean_model = (model or "").strip() or DEFAULT_GOOGLE_API_MODEL
        budget = (
            cls._parse_positive_decimal(monthly_budget_usd, DEFAULT_GOOGLE_API_BUDGET_USD)
            if monthly_budget_usd is not None
            else cls._parse_positive_decimal(existing.get("monthly_budget_usd"), DEFAULT_GOOGLE_API_BUDGET_USD)
        )
        tokens_limit = (
            cls._parse_max_output_tokens(max_output_tokens)
            if max_output_tokens is not None
            else cls._parse_max_output_tokens(existing.get("max_output_tokens"))
        )
        payload = {
            "api_key": clean_key,
            "model": clean_model,
            "monthly_budget_usd": f"{budget:.2f}",
            "max_output_tokens": tokens_limit,
        }
        cfg_path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")
        try:
            os.chmod(cfg_path, 0o600)
        except Exception:
            pass
        cls._login_status_cache.pop("google_api", None)
        cls._login_status_timestamp.pop("google_api", None)
        return cfg_path

    @staticmethod
    def _estimate_google_api_cost(model: str, prompt_tokens: int, output_tokens: int) -> Decimal:
        """按模型保守公开单价估算单次请求费用（USD）。"""
        in_price, out_price = GOOGLE_API_PRICING_USD_PER_M.get(
            (model or "").strip(),
            _DEFAULT_CONSERVATIVE_PRICING,
        )
        million = Decimal("1000000")
        in_cost = (Decimal(max(0, prompt_tokens)) * in_price) / million
        out_cost = (Decimal(max(0, output_tokens)) * out_price) / million
        return (in_cost + out_cost).quantize(Decimal("0.000001"))

    @classmethod
    def get_google_api_monthly_usage(cls, month: str | None = None) -> dict:
        """读取指定月份（默认当月）的 Google API 估算用量。"""
        target_month = month or current_usage_month()
        usage_path = google_api_usage_path()
        default_entry = {
            "month": target_month,
            "requests": 0,
            "prompt_tokens": 0,
            "output_tokens": 0,
            "estimated_cost_usd": "0.000000",
        }
        with _USAGE_LOCK:
            if not usage_path.is_file():
                return default_entry
            try:
                all_data = json.loads(usage_path.read_text(encoding="utf-8"))
                if isinstance(all_data, dict) and isinstance(all_data.get(target_month), dict):
                    entry = all_data[target_month]
                    return {
                        "month": target_month,
                        "requests": int(entry.get("requests") or 0),
                        "prompt_tokens": int(entry.get("prompt_tokens") or 0),
                        "output_tokens": int(entry.get("output_tokens") or 0),
                        "estimated_cost_usd": str(
                            cls._parse_positive_decimal(entry.get("estimated_cost_usd"), Decimal("0"))
                        ),
                    }
            except Exception:
                pass
        return default_entry

    @classmethod
    def record_google_api_usage(cls, model: str, prompt_tokens: int, output_tokens: int) -> dict:
        """累加当月 Google API 请求次数、Token 与估算费用。"""
        target_month = current_usage_month()
        cost = cls._estimate_google_api_cost(model, prompt_tokens, output_tokens)
        usage_path = google_api_usage_path()
        with _USAGE_LOCK:
            usage_path.parent.mkdir(parents=True, exist_ok=True)
            all_data = {}
            if usage_path.is_file():
                try:
                    loaded = json.loads(usage_path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        all_data = loaded
                except Exception:
                    all_data = {}
            cur = all_data.get(target_month) if isinstance(all_data.get(target_month), dict) else {}
            prev_cost = cls._parse_positive_decimal(cur.get("estimated_cost_usd"), Decimal("0"))
            updated = {
                "requests": int(cur.get("requests") or 0) + 1,
                "prompt_tokens": int(cur.get("prompt_tokens") or 0) + max(0, int(prompt_tokens)),
                "output_tokens": int(cur.get("output_tokens") or 0) + max(0, int(output_tokens)),
                "estimated_cost_usd": f"{(prev_cost + cost).quantize(Decimal('0.000001'))}",
                "last_model": model,
                "updated_at": datetime.now().isoformat(timespec="seconds"),
            }
            all_data[target_month] = updated
            usage_path.write_text(json.dumps(all_data, ensure_ascii=False, indent=2), encoding="utf-8")
        cls._login_status_cache.pop("google_api", None)
        cls._login_status_timestamp.pop("google_api", None)
        return {"month": target_month, **updated, "last_call_cost_usd": str(cost)}

    @classmethod
    def set_google_api_monthly_spent(cls, spent_usd: Decimal | str | float, month: str | None = None) -> dict:
        """手动校准当月已用金额（例如对照 GCP/AI Studio 账单归零或对齐）。"""
        target_month = month or current_usage_month()
        spent = cls._parse_positive_decimal(spent_usd, Decimal("0")).quantize(Decimal("0.000001"))
        usage_path = google_api_usage_path()
        with _USAGE_LOCK:
            usage_path.parent.mkdir(parents=True, exist_ok=True)
            all_data = {}
            if usage_path.is_file():
                try:
                    loaded = json.loads(usage_path.read_text(encoding="utf-8"))
                    if isinstance(loaded, dict):
                        all_data = loaded
                except Exception:
                    all_data = {}
            cur = all_data.get(target_month) if isinstance(all_data.get(target_month), dict) else {}
            cur["estimated_cost_usd"] = str(spent)
            cur["calibrated_at"] = datetime.now().isoformat(timespec="seconds")
            all_data[target_month] = cur
            usage_path.write_text(json.dumps(all_data, ensure_ascii=False, indent=2), encoding="utf-8")
        cls._login_status_cache.pop("google_api", None)
        cls._login_status_timestamp.pop("google_api", None)
        return cls.get_google_api_monthly_usage(target_month)

    @classmethod
    def _check_google_api_status(cls) -> dict:
        cfg = cls.get_google_api_config()
        api_key = cfg.get("api_key", "")
        model = cfg.get("model") or DEFAULT_GOOGLE_API_MODEL
        source = cfg.get("source") or "本地配置"
        budget = cls._parse_positive_decimal(cfg.get("monthly_budget_usd"), DEFAULT_GOOGLE_API_BUDGET_USD)
        usage = cls.get_google_api_monthly_usage()
        spent = cls._parse_positive_decimal(usage.get("estimated_cost_usd"), Decimal("0"))
        budget_exceeded = budget > Decimal("0") and spent >= budget
        budget_warning = budget > Decimal("0") and not budget_exceeded and spent >= (budget * Decimal("0.8"))

        if api_key:
            return {
                "installed": True,
                "logged_in": not budget_exceeded,
                "budget_exceeded": budget_exceeded,
                "budget_warning": budget_warning,
                "spent_usd": f"{spent:.2f}",
                "budget_usd": f"{budget:.2f}",
                "account_type": f"{model} · ${spent:.2f}/${budget:.2f}",
                "model": model,
                "source": source,
                "message": (
                    f"已触发月度预算熔断（本月预估 ${spent:.2f} / 上限 ${budget:.2f}），已禁止继续调用"
                    if budget_exceeded
                    else f"Google API 已就绪 ({source} · 模型 {model} · 本月 ${spent:.2f}/${budget:.2f})"
                ),
            }
        return {
            "installed": True,
            "logged_in": False,
            "budget_exceeded": False,
            "budget_warning": False,
            "spent_usd": f"{spent:.2f}",
            "budget_usd": f"{budget:.2f}",
            "account_type": "",
            "model": model,
            "source": "",
            "message": "未配置 Google API Key，请点击「配置 Google API」或设置 GEMINI_API_KEY",
        }

    @classmethod
    def check_login_status(cls, force: bool = False, cache_ttl: float = 120.0, provider: str = "codex") -> dict:
        """
        检查 AI 引擎（codex / gemini / google_api）可用状态（带按引擎缓存与超时防卡死机制）
        :param force: 是否强制重新检测外部进程（默认 False，复用有效缓存）
        :param cache_ttl: 缓存有效期（秒），默认 120 秒
        :param provider: AI 引擎标识（"codex"、"gemini" 或 "google_api"）
        :return: 包含 installed, logged_in, account_type, message, provider 等状态字典
        """
        prov = normalize_ai_provider(provider)
        now = time.time()
        cached = cls._login_status_cache.get(prov)
        cached_at = cls._login_status_timestamp.get(prov, 0.0)
        if not force and cached is not None and (now - cached_at < cache_ttl):
            return cached

        if prov == "gemini":
            res_dict = {**cls._check_gemini_login_status(), "provider": "gemini"}
            cls._login_status_cache[prov] = res_dict
            cls._login_status_timestamp[prov] = now
            return res_dict

        if prov == "google_api":
            res_dict = {**cls._check_google_api_status(), "provider": "google_api"}
            cls._login_status_cache[prov] = res_dict
            cls._login_status_timestamp[prov] = now
            return res_dict

        cli = cls.get_cli_path()
        if not cli:
            res_dict = {
                "provider": "codex",
                "installed": False,
                "logged_in": False,
                "account_type": "",
                "message": "未检测到 codex CLI，请确认已安装到 PATH 或 ~/.local/bin/codex",
            }
            cls._login_status_cache[prov] = res_dict
            cls._login_status_timestamp[prov] = now
            return res_dict

        try:
            res = subprocess.run([cli, "login", "status"], capture_output=True, text=True, timeout=8)
            output = (res.stdout + res.stderr).strip()
            is_logged_in = res.returncode == 0 and "Logged in" in output
            account_type = ""
            if "Logged in using" in output:
                account_type = output.split("Logged in using", 1)[1].strip()
            elif is_logged_in:
                account_type = "已认证"

            res_dict = {
                "provider": "codex",
                "installed": True,
                "logged_in": is_logged_in,
                "account_type": account_type,
                "message": output if is_logged_in else "未登录或登录已失效，请在终端执行 codex login",
            }
            cls._login_status_cache[prov] = res_dict
            cls._login_status_timestamp[prov] = now
            return res_dict
        except subprocess.TimeoutExpired:
            return {"provider": "codex", "installed": True, "logged_in": False, "account_type": "", "message": "检查登录超时"}
        except Exception as e:
            return {"provider": "codex", "installed": True, "logged_in": False, "account_type": "", "message": f"状态检查异常: {e}"}

    @classmethod
    def build_portfolio_context(
        cls,
        analytics,
        max_positions: int = 15,
        person_name: str | None = None,
        channel_names: tuple[str, ...] | None = None,
        channel: str | None = None,
        symbol: str | None = None,
    ) -> str:
        """
        从当前 Analytics 对象提取结构化资产与交易记录上下文供 AI 分析，
        严格支持根据用户选择的【归属人员】、【交易渠道】、【目标标的 Ticker】进行高精度聚焦过滤与交易记录自动穿透。
        """
        if not analytics or not getattr(analytics, "reports", None):
            return "【暂无持仓或数据库未加载】"

        if channel and not channel_names:
            channel_names = (channel,)

        lines = ["=== 用户当前投资账本分析上下文 ==="]

        # 1. 规范化筛选标签
        target_person = person_name.strip() if person_name and person_name not in ("全部启用人员", "全部人员") else None
        target_channels = tuple(ch.strip() for ch in channel_names) if channel_names and "全部渠道" not in channel_names else None
        target_sym = symbol.split(" · ")[0].strip().upper() if symbol and symbol != "全部股票" else None

        scope_desc = [
            f"归属人员: {target_person or '全部人员'}",
            f"交易渠道: {','.join(target_channels) if target_channels else '全部渠道'}",
            f"目标标的: {target_sym or '全部股票'}",
        ]
        lines.append(f"【用户当前聚焦分析范围】: {' | '.join(scope_desc)}")

        # 2. 投资组合综合指标 (若为全部人员或特定人员)
        if getattr(analytics, "metrics", None):
            metric_strs = []
            for m in analytics.metrics:
                if m.value is not None:
                    metric_strs.append(f"{m.name}: {_fmt(m.value)}")
            if metric_strs:
                lines.append("\n【投资组合综合指标】: " + " | ".join(metric_strs[:6]))

        # 3. 针对特定人员或特定标的提取【持仓与资产状态】
        # 优先读取「人员资产」报表（包含具体人员归属、渠道、状态、成本与清仓表现）
        person_assets = analytics.reports.get("人员资产")
        matched_positions = []
        if person_assets and person_assets.rows:
            for row in person_assets.rows:
                # 0: 人员, 1: 渠道, 2: Ticker, 3: 数量, 4: 当前状态, 5: 持仓成本/清仓成本($), 6: 最新价($), 7: 当前市值($), 8: 持仓盈亏/清仓收益($), 9: 盈亏比例, 10: 回本涨幅/清仓后涨跌
                r_person = str(row.values[0]) if len(row.values) > 0 else ""
                r_channel = str(row.values[1]) if len(row.values) > 1 else ""
                r_ticker = str(row.values[2]).upper() if len(row.values) > 2 else ""

                if target_person and target_person not in r_person:
                    continue
                if target_channels and not any(ch in r_channel for ch in target_channels):
                    continue
                if target_sym and r_ticker != target_sym:
                    continue

                matched_positions.append(row)

        if target_sym:
            lines.append(f"\n【聚焦标的「{target_sym}」的持仓与盈亏状态】:")
            if matched_positions:
                for row in matched_positions:
                    p_name = row.values[0] if len(row.values) > 0 else ""
                    c_name = row.values[1] if len(row.values) > 1 else ""
                    qty = row.values[3] if len(row.values) > 3 else None
                    status = row.values[4] if len(row.values) > 4 else ""
                    cost = row.values[5] if len(row.values) > 5 else None
                    price = row.values[6] if len(row.values) > 6 else None
                    gain = row.values[8] if len(row.values) > 8 else None
                    gain_pct = row.values[9] if len(row.values) > 9 else None
                    rebound = row.values[10] if len(row.values) > 10 else None

                    if status == "持有中":
                        lines.append(
                            f"• 人员:【{p_name}】 渠道:【{c_name}】 [持有中]\n"
                            f"  持仓股数: {_fmt(qty)} 股 | 持仓成本均价: ${_fmt(cost)} | 最新市价: ${_fmt(price)}\n"
                            f"  持仓浮动盈亏: ${_fmt(gain, signed=True)} (收益率: {_fmt(gain_pct, signed=True, as_percent=True)})\n"
                            f"  回本所需涨幅: {_fmt(rebound, signed=True, as_percent=True)}"
                        )
                    else:
                        verdict = "清仓后大涨(卖飞)" if (isinstance(rebound, Decimal) and rebound > 0) else "清仓后下跌(逃顶避险)" if isinstance(rebound, Decimal) else ""
                        lines.append(
                            f"• 人员:【{p_name}】 渠道:【{c_name}】 [已清仓]\n"
                            f"  累计清仓买入均价: ${_fmt(cost)} | 清仓已实现收益: ${_fmt(gain, signed=True)}\n"
                            f"  清仓后涨跌表现: {_fmt(rebound, signed=True, as_percent=True)} {f'({verdict})' if verdict else ''}"
                        )
            else:
                lines.append(f"• 在当前筛选范围（人员: {target_person or '全部'}, 渠道: {target_channels or '全部'}）下，暂未查到 {target_sym} 的持仓记录。")

            # 4. 重点穿透提取该标的的【真实历史交易流水记录】
            trades_report = analytics.reports.get("交易回放")
            if trades_report and trades_report.rows:
                matched_trades = []
                for row in trades_report.rows:
                    t_sym = str(row.values[3] if len(row.values) > 3 else "").upper()
                    if t_sym != target_sym:
                        continue
                    t_person = str(row.metadata.get("person_name") or "")
                    if target_person and target_person not in t_person:
                        continue
                    t_channel = str(row.values[1] if len(row.values) > 1 else "")
                    if target_channels and not any(ch in t_channel for ch in target_channels):
                        continue
                    matched_trades.append(row)

                if matched_trades:
                    lines.append(f"\n【「{target_sym}」在当前范围下的全部历史真实交易流水 (按时间倒序，共 {len(matched_trades)} 笔)】:")
                    for i, r in enumerate(matched_trades[:12]):
                        dt = str(r.values[0])[:16]
                        ch = r.values[1]
                        person = r.metadata.get("person_name", "")
                        side = r.values[4]
                        qty = r.values[5]
                        price = r.values[6]
                        post_qty = r.values[9]
                        post_cost = r.values[11]
                        cost_diff = r.metadata.get("cost_diff")
                        diff_text = f" | 成本变化:{_fmt(cost_diff, signed=True)}" if cost_diff is not None else ""
                        lines.append(
                            f"  {i+1}. [{dt}] {ch} {person} {side} {_fmt(qty)}股 @ ${_fmt(price)} | 操作后持仓:{_fmt(post_qty)}股 | 操作后成本:${_fmt(post_cost)}{diff_text}"
                        )
                else:
                    lines.append(f"• 在当前筛选范围内未找到 {target_sym} 的历史成交记录。")

        else:
            # 未指定特定标的时，分类全量输出当前范围的持仓与已清仓标的
            holding_rows = [r for r in matched_positions if len(r.values) > 4 and r.values[4] == "持有中"]
            closed_rows = [r for r in matched_positions if len(r.values) > 4 and r.values[4] == "已清仓"]

            lines.append(
                f"\n【当前范围全量标的档案概况（共 {len(matched_positions)} 只标的：持有中 {len(holding_rows)} 只，已清仓 {len(closed_rows)} 只）】:"
            )

            if holding_rows:
                lines.append(f"\n--- 【当前持有中标的（共 {len(holding_rows)} 只）】 ---")
                for r in holding_rows:
                    p_name = r.values[0] if len(r.values) > 0 else ""
                    c_name = r.values[1] if len(r.values) > 1 else ""
                    t_name = r.values[2] if len(r.values) > 2 else ""
                    qty = r.values[3] if len(r.values) > 3 else None
                    cost = r.values[5] if len(r.values) > 5 else None
                    price = r.values[6] if len(r.values) > 6 else None
                    gain = r.values[8] if len(r.values) > 8 else None
                    gain_pct = r.values[9] if len(r.values) > 9 else None
                    lines.append(
                        f"• {t_name} ({c_name}): 股数={_fmt(qty)}, 成本=${_fmt(cost)}, 现价=${_fmt(price)}, 浮亏=${_fmt(gain, signed=True)} ({_fmt(gain_pct, signed=True, as_percent=True)})"
                    )

            if closed_rows:
                lines.append(f"\n--- 【历史已清仓标的（共 {len(closed_rows)} 只）】 ---")
                for r in closed_rows:
                    c_name = r.values[1] if len(r.values) > 1 else ""
                    t_name = r.values[2] if len(r.values) > 2 else ""
                    cost = r.values[5] if len(r.values) > 5 else None
                    gain = r.values[8] if len(r.values) > 8 else None
                    rebound = r.values[10] if len(r.values) > 10 else None
                    lines.append(
                        f"• {t_name} ({c_name}): 累计买入均价=${_fmt(cost)}, 清仓已实现收益=${_fmt(gain, signed=True)}, 清仓后表现={_fmt(rebound, signed=True, as_percent=True)}"
                    )

            # 在宏观模式下，同样穿透注入当前范围的最新真实逐笔成交流水（包含成交时间、数量、金额、费用与做T成本变动）
            trades_report = analytics.reports.get("交易回放")
            if trades_report and trades_report.rows:
                matched_trades = []
                for row in trades_report.rows:
                    t_person = str(row.metadata.get("person_name") or "")
                    if target_person and target_person not in t_person:
                        continue
                    t_channel = str(row.values[1] if len(row.values) > 1 else "")
                    if target_channels and not any(ch in t_channel for ch in target_channels):
                        continue
                    matched_trades.append(row)

                if matched_trades:
                    lines.append(f"\n【当前范围最新真实逐笔历史成交流水（SQLite 数据库真实记录，按时间倒序展示最近 20 笔，全量共有 {len(matched_trades)} 笔记录）】:")
                    for i, r in enumerate(matched_trades[:20]):
                        dt = str(r.values[0])[:16]
                        ch = r.values[1]
                        sym_code = r.values[3]
                        person = r.metadata.get("person_name", "")
                        side = r.values[4]
                        qty = r.values[5]
                        price = r.values[6]
                        amount = r.values[7] if len(r.values) > 7 else None
                        post_qty = r.values[9] if len(r.values) > 9 else None
                        post_cost = r.values[10] if len(r.values) > 10 else None
                        cost_diff = r.metadata.get("cost_diff")
                        fee = r.metadata.get("fee") or r.metadata.get("commission")
                        diff_text = f" | 成本变化:{_fmt(cost_diff, signed=True)}" if cost_diff is not None else ""
                        fee_text = f" | 费用:${_fmt(fee)}" if fee else ""
                        lines.append(
                            f"  {i+1}. [{dt}] {ch} {person} {sym_code} {side} {_fmt(qty)}股 @ ${_fmt(price)} (成交金额:${_fmt(amount)}) | 操作后持仓:{_fmt(post_qty)}股 | 操作后成本:${_fmt(post_cost)}{diff_text}{fee_text}"
                        )

        return "\n".join(lines)

    @classmethod
    def _run_cli_process(
        cls,
        cmd: list[str],
        *,
        provider_label: str,
        stdout_path: str,
        stderr_path: str | None = None,
        cwd: str | None = None,
        env: dict[str, str] | None = None,
        cancel_event: threading.Event | None = None,
        max_timeout: float = 300.0,
    ) -> int:
        """统一执行外部 AI CLI 子进程并支持实时取消与超时保护，防止 PIPE 阻塞。"""
        proc = None
        start_time = time.time()
        try:
            with open(stdout_path, "w", encoding="utf-8") as out_f:
                err_ctx = open(stderr_path, "w", encoding="utf-8") if stderr_path else None
                try:
                    proc = subprocess.Popen(
                        cmd,
                        stdin=subprocess.DEVNULL,
                        stdout=out_f,
                        stderr=err_ctx if err_ctx else out_f,
                        cwd=cwd,
                        env=env,
                        text=True,
                    )
                    while proc.poll() is None:
                        if cancel_event and cancel_event.is_set():
                            proc.kill()
                            raise RuntimeError("用户已中止当前 AI 问答请求。")
                        if time.time() - start_time > max_timeout:
                            proc.kill()
                            raise RuntimeError(
                                f"{provider_label} 执行超时（超过 {int(max_timeout)} 秒未完成），已自动终止以防卡死。"
                            )
                        try:
                            proc.wait(timeout=0.2)
                        except subprocess.TimeoutExpired:
                            continue
                    return int(proc.returncode or 0)
                finally:
                    if err_ctx:
                        err_ctx.close()
        finally:
            if proc and proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass

    @staticmethod
    def _extract_gemini_json_response(stdout_text: str) -> str | None:
        """解析 agy / gemini CLI 输出中的 JSON response 字段（容错前后日志或警告行）。"""
        candidates = [stdout_text.strip()]
        for line in reversed(stdout_text.splitlines()):
            s = line.strip()
            if s.startswith("{") and s.endswith("}"):
                candidates.append(s)
        for cand in candidates:
            if not cand:
                continue
            try:
                parsed = json.loads(cand)
            except json.JSONDecodeError:
                continue
            if isinstance(parsed, dict):
                if parsed.get("error"):
                    raise RuntimeError(str(parsed["error"]))
                resp = parsed.get("response")
                if isinstance(resp, str) and resp.strip():
                    return resp.strip()
        return None

    @classmethod
    def _ask_gemini(
        cls,
        full_prompt: str,
        *,
        model: str | None = None,
        cancel_event: threading.Event | None = None,
    ) -> str:
        """通过本地 Antigravity CLI (agy) 或 Gemini CLI 非交互模式发起只读问答。"""
        has_env_key = bool(os.environ.get("GEMINI_API_KEY") or os.environ.get("GOOGLE_API_KEY"))
        gemini_cli = cls.get_gemini_cli_path()
        agy_cli = cls.get_agy_cli_path()

        use_gemini_cli = bool(gemini_cli and (has_env_key or not agy_cli))
        cli = gemini_cli if use_gemini_cli else agy_cli
        if not cli:
            raise RuntimeError("未在系统中找到 agy 或 gemini 命令，请先安装 Antigravity CLI 或配置 GEMINI_API_KEY。")

        temp_out = tempfile.NamedTemporaryFile(suffix=".json", delete=False)
        out_path = temp_out.name
        temp_out.close()
        log_out = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
        err_path = log_out.name
        log_out.close()

        env = os.environ.copy()
        extra_bins = [os.path.dirname(cli), "/opt/homebrew/bin", "/usr/local/bin"]
        existing_path = env.get("PATH", "")
        env["PATH"] = os.pathsep.join(dict.fromkeys([*extra_bins, *(existing_path.split(os.pathsep) if existing_path else [])]))

        if use_gemini_cli:
            cmd = [cli, "--skip-trust", "--approval-mode", "plan", "-p", full_prompt, "--output-format", "json"]
            if model:
                cmd.extend(["-m", model])
            provider_label = "Gemini CLI"
        else:
            chosen_model = model or "gemini-4-argon-medium"
            cmd = [
                cli,
                "-p",
                full_prompt,
                "--mode",
                "plan",
                "--sandbox",
                "--output-format",
                "json",
                "--model",
                chosen_model,
            ]
            provider_label = "Gemini (Antigravity CLI)"

        try:
            rc = cls._run_cli_process(
                cmd,
                provider_label=provider_label,
                stdout_path=out_path,
                stderr_path=err_path,
                cwd=tempfile.gettempdir(),
                env=env,
                cancel_event=cancel_event,
            )
            stdout_text = Path(out_path).read_text(encoding="utf-8", errors="replace").strip() if Path(out_path).exists() else ""
            stderr_text = Path(err_path).read_text(encoding="utf-8", errors="replace").strip() if Path(err_path).exists() else ""
            if rc != 0:
                err_msg = stderr_text[-2000:] or stdout_text[-2000:] or f"退出码 {rc}"
                if "UNSUPPORTED_CLIENT" in err_msg or "IneligibleTierError" in err_msg:
                    raise RuntimeError(
                        "Google 个人账号已不再支持旧版 gemini-cli；请改用本机 Antigravity CLI (agy) 或配置 GEMINI_API_KEY。"
                    )
                raise RuntimeError(f"{provider_label} 执行失败: {err_msg.strip()}")
            if stdout_text:
                extracted = cls._extract_gemini_json_response(stdout_text)
                if extracted:
                    return extracted
                return stdout_text
            return stderr_text[-2000:] if stderr_text else f"{provider_label} 未返回有效内容。"
        finally:
            Path(out_path).unlink(missing_ok=True)
            Path(err_path).unlink(missing_ok=True)

    @classmethod
    def _ask_google_api(
        cls,
        full_prompt: str,
        *,
        model: str | None = None,
        cancel_event: threading.Event | None = None,
        timeout: float = 180.0,
    ) -> str:
        """通过 Google Gemini Developer API (REST) 发起只读问答，并执行月度预算硬熔断。"""
        cfg = cls.get_google_api_config()
        api_key = cfg.get("api_key", "").strip()
        if not api_key:
            raise RuntimeError("未配置 Google API Key，请先在界面点击「配置 Google API」或设置环境变量 GEMINI_API_KEY。")

        chosen_model = (model or "").strip() or cfg.get("model") or DEFAULT_GOOGLE_API_MODEL
        budget = cls._parse_positive_decimal(cfg.get("monthly_budget_usd"), DEFAULT_GOOGLE_API_BUDGET_USD)
        max_output_tokens = cls._parse_max_output_tokens(cfg.get("max_output_tokens"))
        usage = cls.get_google_api_monthly_usage()
        spent = cls._parse_positive_decimal(usage.get("estimated_cost_usd"), Decimal("0"))

        # 预估输入 token 数（约 3 字符/token，宁可略高估）并在请求前做硬性预算熔断
        est_prompt_tokens = max(1, len(full_prompt) // 3)
        est_input_cost = cls._estimate_google_api_cost(chosen_model, est_prompt_tokens, 0)
        if budget > Decimal("0") and (spent >= budget or spent + est_input_cost > budget):
            raise RuntimeError(
                f"已触发 Google API 月度预算熔断：本月已预估使用 ${spent:.2f} / 安全上限 ${budget:.2f}。"
                f"为防止超出每月 $10 免费额度产生扣费，已拦截本次请求。"
            )

        encoded_model = urllib.parse.quote(chosen_model, safe="")
        url = f"{_GOOGLE_API_BASE_URL}/{encoded_model}:generateContent"
        payload = json.dumps(
            {
                "contents": [{"parts": [{"text": full_prompt}]}],
                "generationConfig": {"maxOutputTokens": max_output_tokens},
            },
            ensure_ascii=False,
        ).encode("utf-8")
        req = urllib.request.Request(
            url,
            data=payload,
            method="POST",
            headers={
                "Content-Type": "application/json",
                "x-goog-api-key": api_key,
            },
        )

        result_box: dict[str, object] = {}
        done_event = threading.Event()

        def _do_request():
            try:
                with urllib.request.urlopen(req, timeout=timeout) as resp:
                    result_box["body"] = resp.read().decode("utf-8", errors="replace")
            except urllib.error.HTTPError as exc:
                err_body = ""
                try:
                    err_body = exc.read().decode("utf-8", errors="replace")
                except Exception:
                    pass
                result_box["http_error"] = (exc.code, err_body)
            except Exception as exc:
                result_box["error"] = exc
            finally:
                done_event.set()

        worker = threading.Thread(target=_do_request, daemon=True)
        worker.start()

        start_time = time.time()
        while not done_event.wait(timeout=0.2):
            if cancel_event and cancel_event.is_set():
                raise RuntimeError("用户已中止当前 AI 问答请求。")
            if time.time() - start_time > timeout:
                raise RuntimeError(f"Google API 请求超时（超过 {int(timeout)} 秒未响应）。")

        if "http_error" in result_box:
            code, err_body = result_box["http_error"]  # type: ignore[misc]
            detail = err_body
            try:
                parsed_err = json.loads(err_body)
                if isinstance(parsed_err, dict) and isinstance(parsed_err.get("error"), dict):
                    detail = str(parsed_err["error"].get("message") or err_body)
            except Exception:
                pass
            if code in (401, 403):
                raise RuntimeError(f"Google API 鉴权失败 (HTTP {code})，请检查 API Key 是否有效: {detail}")
            if code == 429:
                raise RuntimeError(f"Google API 配额或速率受限 (HTTP 429): {detail}")
            raise RuntimeError(f"Google API 请求失败 (HTTP {code}): {detail}")

        if "error" in result_box:
            raise RuntimeError(f"Google API 网络请求异常: {result_box['error']}")

        raw_body = str(result_box.get("body") or "")
        try:
            data = json.loads(raw_body)
        except json.JSONDecodeError as exc:
            raise RuntimeError(f"Google API 返回非 JSON 响应: {exc}") from exc

        if isinstance(data, dict) and isinstance(data.get("error"), dict):
            raise RuntimeError(f"Google API 返回错误: {data['error'].get('message') or data['error']}")

        candidates = data.get("candidates") if isinstance(data, dict) else None
        text_parts: list[str] = []
        if isinstance(candidates, list) and candidates:
            content = candidates[0].get("content") if isinstance(candidates[0], dict) else None
            parts = content.get("parts") if isinstance(content, dict) else None
            if isinstance(parts, list):
                for part in parts:
                    if isinstance(part, dict) and isinstance(part.get("text"), str):
                        text_parts.append(part["text"])
        answer = "\n".join(text_parts).strip()

        # 统计并记录本次真实 Token 消耗与估算费用
        usage_meta = data.get("usageMetadata") if isinstance(data, dict) else None
        if isinstance(usage_meta, dict):
            prompt_tokens = int(usage_meta.get("promptTokenCount") or est_prompt_tokens)
            cand_tokens = int(usage_meta.get("candidatesTokenCount") or 0)
            thought_tokens = int(usage_meta.get("thoughtsTokenCount") or 0)
            output_tokens = cand_tokens + thought_tokens
        else:
            prompt_tokens = est_prompt_tokens
            output_tokens = max(0, len(answer) // 3)
        cls.record_google_api_usage(chosen_model, prompt_tokens, output_tokens)

        if answer:
            return answer
        finish_reason = (
            candidates[0].get("finishReason", "") if isinstance(candidates, list) and candidates and isinstance(candidates[0], dict) else ""
        )
        raise RuntimeError(f"Google API 未返回有效文本内容（finishReason={finish_reason or '未知'}）。")

    @classmethod
    def ask(
        cls,
        prompt: str,
        context: str = "",
        model: str | None = None,
        cancel_event: threading.Event | None = None,
        provider: str = "codex",
    ) -> str:
        """
        调用本地 AI CLI（codex / gemini）或 Google API 发起查询，安全且只读模式返回结果
        :param prompt: 用户提问
        :param context: 结构化上下文背景
        :param model: 可选模型，缺省时使用系统默认模型
        :param cancel_event: 允许外部取消任务
        :param provider: AI 引擎（"codex" 默认、"gemini" 或 "google_api"）
        :return: AI 最终文本回复
        """
        prov = normalize_ai_provider(provider)
        full_prompt = prompt.strip()
        if context:
            full_prompt = (
                f"你是一个嵌入在专业个人股票资产管理软件中的资深投资顾问与分析助理。\n"
                f"用户的软件直连真实的 SQLite 本地只读投资账本数据库，数据库中完整保留了用户的每一笔历史成交时间、数量、单价、发生金额、手续费及持仓成本变化记录。\n"
                f"系统已经为你注入了当前筛选范围内的全量资产持仓档案以及最近的逐笔真实成交历史流水明细。\n\n"
                f"{context}\n\n"
                f"【用户的问题/指令】:\n{full_prompt}\n\n"
                f"【回复要求】:\n"
                f"1. 务必直接针对用户当前指定的标的、人员、渠道数据及成交流水进行诊断，引用真实的成本、数量和逐笔成交流水事实，切勿谎称数据库缺少成交时间或费用；\n"
                f"2. 请使用清晰规范的 Markdown 格式排版（支持加粗、列表、表格），风格客观犀利，聚焦投资风险与做T/风控策略，避免泛泛空话。"
            )

        if prov == "gemini":
            return cls._ask_gemini(full_prompt, model=model, cancel_event=cancel_event)
        if prov == "google_api":
            return cls._ask_google_api(full_prompt, model=model, cancel_event=cancel_event)

        cli = cls.get_cli_path()
        if not cli:
            raise RuntimeError("未在系统中找到 codex 命令，请先确保已安装并加入 PATH。")

        temp_out = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
        temp_path = temp_out.name
        temp_out.close()

        log_out = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
        log_path = log_out.name
        log_out.close()

        try:
            cmd = [
                cli, "exec",
                "--skip-git-repo-check",
                "--sandbox", "read-only",
                "--ephemeral",
                "-o", temp_path,
            ]
            if model:
                cmd.extend(["-m", model])
            cmd.append(full_prompt)

            rc = cls._run_cli_process(
                cmd,
                provider_label="Codex",
                stdout_path=log_path,
                cancel_event=cancel_event,
            )
            log_p = Path(log_path)
            log_content = log_p.read_text(encoding="utf-8", errors="replace").strip() if log_p.exists() else ""

            if rc != 0:
                err_msg = log_content[-2000:] if log_content else f"退出码 {rc}"
                raise RuntimeError(f"Codex 执行失败: {err_msg.strip()}")

            out_p = Path(temp_path)
            if out_p.exists():
                content = out_p.read_text(encoding="utf-8", errors="replace").strip()
                if content:
                    return content

            return log_content[-2000:] if log_content else "Codex 未返回有效内容。"
        finally:
            Path(temp_path).unlink(missing_ok=True)
            Path(log_path).unlink(missing_ok=True)

    @classmethod
    def ask_stock(
        cls,
        prompt: str,
        symbol: str | None = None,
        model: str | None = None,
        cancel_event: threading.Event | None = None,
        provider: str = "codex",
    ) -> str:
        """
        纯粹针对标的股票本身进行基本面、财报、业务前景与竞争格局的分析问答，不依赖用户数据库账本。
        """
        sym_clean = symbol.split(" · ")[0].strip().upper() if symbol and symbol != "全部股票" else ""
        if sym_clean:
            full_prompt = (
                f"你是一位华尔街资深证券分析师与美股行业研究专家。\n"
                f"用户当前聚焦研究的标的股票为：【{sym_clean}】。\n"
                f"请纯粹围绕【{sym_clean}】这家公司本身展开深入、客观、有理有据的证券研究与商业分析。\n"
                f"你可以综合考虑其商业模式、最新财报核心财务数据（营收、利润率、自由现金流等）、行业竞争壁垒与护城河、未来催化剂与增长前景，以及潜在下行风险。\n\n"
                f"【用户的问题/指令】:\n{prompt.strip()}\n\n"
                f"【回复要求】:\n"
                f"1. 务必紧扣【{sym_clean}】标的本身，直接回答用户的提问，观点清晰犀利、逻辑严密；\n"
                f"2. 请使用规范清晰的 Markdown 格式排版（支持加粗、列表、表格），风格专业客观，避免空洞套话。"
            )
        else:
            full_prompt = (
                f"你是一位华尔街资深证券分析师与行业研究专家。\n"
                f"请围绕美股市场行情、行业板块趋势或宏观催化剂展开客观深入的证券商业分析。\n\n"
                f"【用户的问题/指令】:\n{prompt.strip()}\n\n"
                f"【回复要求】:\n"
                f"请使用规范清晰的 Markdown 格式排版，观点明确、逻辑严谨。"
            )
        return cls.ask(full_prompt, context="", model=model, cancel_event=cancel_event, provider=provider)
