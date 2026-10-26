"""Codex CLI AI Service.

Bridges the local Codex CLI subscription to provide AI investment assistant capabilities
without requiring manual API key configuration or extra token billing.
Supports multi-dimensional contextual filtering across Person, Channel, and Ticker.
"""
from decimal import Decimal
import os
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import time


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
        default_local = Path.home() / ".local/bin/codex"
        if default_local.exists() and os.access(default_local, os.X_OK):
            return str(default_local)
        return None

    _login_status_cache: dict | None = None
    _login_status_timestamp: float = 0.0

    @classmethod
    def check_login_status(cls, force: bool = False, cache_ttl: float = 120.0) -> dict:
        """
        检查 Codex CLI 安装与登录状态（带内存缓存与超时防卡死机制）
        :param force: 是否强制重新检测外部进程（默认 False，复用有效缓存）
        :param cache_ttl: 缓存有效期（秒），默认 120 秒
        :return: 包含 installed, logged_in, account_type, message 等状态字典
        """
        now = time.time()
        if not force and cls._login_status_cache is not None and (now - cls._login_status_timestamp < cache_ttl):
            return cls._login_status_cache

        cli = cls.get_cli_path()
        if not cli:
            res_dict = {
                "installed": False,
                "logged_in": False,
                "account_type": "",
                "message": "未检测到 codex CLI，请确认已安装到 PATH 或 ~/.local/bin/codex",
            }
            cls._login_status_cache = res_dict
            cls._login_status_timestamp = now
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
                "installed": True,
                "logged_in": is_logged_in,
                "account_type": account_type,
                "message": output if is_logged_in else "未登录或登录已失效，请在终端执行 codex login",
            }
            cls._login_status_cache = res_dict
            cls._login_status_timestamp = now
            return res_dict
        except subprocess.TimeoutExpired:
            return {"installed": True, "logged_in": False, "account_type": "", "message": "检查登录超时"}
        except Exception as e:
            return {"installed": True, "logged_in": False, "account_type": "", "message": f"状态检查异常: {e}"}

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
    def ask(
        cls,
        prompt: str,
        context: str = "",
        model: str | None = None,
        cancel_event: threading.Event | None = None,
    ) -> str:
        """
        调用本地 Codex CLI 发起查询，安全且只读模式返回结果
        :param prompt: 用户提问
        :param context: 结构化上下文背景
        :param model: 可选模型，缺省时使用系统默认模型
        :param cancel_event: 允许外部取消任务
        :return: AI 最终文本回复
        """
        cli = cls.get_cli_path()
        if not cli:
            raise RuntimeError("未在系统中找到 codex 命令，请先确保已安装并加入 PATH。")

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

        temp_out = tempfile.NamedTemporaryFile(suffix=".txt", delete=False)
        temp_path = temp_out.name
        temp_out.close()

        log_out = tempfile.NamedTemporaryFile(suffix=".log", delete=False)
        log_path = log_out.name
        log_out.close()

        proc = None
        start_time = time.time()
        max_timeout = 300.0  # 5分钟超时保护

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

            # 将 stdout 和 stderr 直接重定向到文件流，并在有效 context 内等待，彻底杜绝 PIPE 满缓冲区造成的操作系统级死锁
            with open(log_path, "w", encoding="utf-8") as log_file:
                proc = subprocess.Popen(
                    cmd,
                    stdout=log_file,
                    stderr=log_file,
                    text=True,
                )

                while proc.poll() is None:
                    if cancel_event and cancel_event.is_set():
                        proc.kill()
                        raise RuntimeError("用户已中止当前 AI 问答请求。")
                    if time.time() - start_time > max_timeout:
                        proc.kill()
                        raise RuntimeError(f"Codex 执行超时（超过 {int(max_timeout)} 秒未完成），已自动终止以防卡死。")
                    try:
                        proc.wait(timeout=0.2)
                    except subprocess.TimeoutExpired:
                        continue

            log_p = Path(log_path)
            log_content = log_p.read_text(encoding="utf-8", errors="replace").strip() if log_p.exists() else ""

            if proc.returncode != 0:
                err_msg = log_content[-2000:] if log_content else f"退出码 {proc.returncode}"
                raise RuntimeError(f"Codex 执行失败: {err_msg.strip()}")

            out_p = Path(temp_path)
            if out_p.exists():
                content = out_p.read_text(encoding="utf-8", errors="replace").strip()
                if content:
                    return content

            return log_content[-2000:] if log_content else "Codex 未返回有效内容。"
        finally:
            if proc and proc.poll() is None:
                try:
                    proc.kill()
                except Exception:
                    pass
            Path(temp_path).unlink(missing_ok=True)
            Path(log_path).unlink(missing_ok=True)

    @classmethod
    def ask_stock(
        cls,
        prompt: str,
        symbol: str | None = None,
        model: str | None = None,
        cancel_event: threading.Event | None = None,
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
        return cls.ask(full_prompt, context="", model=model, cancel_event=cancel_event)
