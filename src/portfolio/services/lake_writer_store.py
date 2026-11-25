"""拉科专属 Gemini 写作空间持久化存储（多对话框标签 + 软清空/软删除归档）。

Web 端与 Mac 桌面端共用本模块与同一本地 JSON 文件：
- 普通用户清空对话时，当前消息不会物理删除，而是按批次快照归档到 archived_batches；
- 普通用户关闭/删除标签时，标签不会物理删除，而是标记为 status="deleted"；
- 管理员（Web 端）与 Mac 客户端均可只读查看所有账号的活跃会话、已关闭标签及全部历史清空存档。
"""
from __future__ import annotations

import json
import logging
import os
import threading
import uuid
from datetime import datetime
from pathlib import Path
from typing import Any, Dict, List, Optional

logger = logging.getLogger(__name__)

_STORE_LOCK = threading.RLock()
LAKE_WRITER_FILENAME = "ai_lake_writer_sessions.json"
MAX_ACTIVE_MESSAGES_PER_TAB = 120


def default_lake_writer_path() -> Path:
    """返回拉科写作会话默认持久化文件路径（支持环境变量覆盖）。"""
    env_path = os.environ.get("MONEY3_LAKE_WRITER_SESSIONS", "").strip()
    if env_path:
        p = Path(env_path).expanduser()
        p.parent.mkdir(parents=True, exist_ok=True)
        return p
    base_dir = Path.home() / "Library" / "Application Support" / "MoneyPortfolio"
    if not base_dir.exists():
        base_dir = Path.home() / ".money_portfolio"
    base_dir.mkdir(parents=True, exist_ok=True)
    return base_dir / LAKE_WRITER_FILENAME


def _now_iso() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _now_hms() -> str:
    return datetime.now().strftime("%H:%M:%S")


def _new_id(prefix: str) -> str:
    return f"{prefix}_{uuid.uuid4().hex[:10]}"


def _auto_title_from_prompt(prompt: str, fallback: str) -> str:
    """从首条写作指令中提取简洁标题（去除换行和多余空白）。"""
    cleaned = " ".join((prompt or "").strip().split())
    if not cleaned:
        return fallback
    return cleaned[:14] + ("…" if len(cleaned) > 14 else "")


def _make_tab(owner: str, title: str, auto_titled: bool = True, messages: Optional[List[dict]] = None) -> Dict[str, Any]:
    now = _now_iso()
    return {
        "id": _new_id("tab"),
        "title": title,
        "auto_titled": auto_titled,
        "owner": owner,
        "status": "active",
        "created_at": now,
        "updated_at": now,
        "deleted_at": None,
        "messages": list(messages or []),
        "archived_batches": [],
    }


def _next_default_title(tabs: List[Dict[str, Any]]) -> str:
    active_count = sum(1 for t in tabs if t.get("status", "active") == "active")
    return f"新文章 {active_count + 1}"


class LakeWriterStore:
    """管理拉科专属写作空间的多标签会话与归档记录。"""

    def __init__(self, path: Optional[Path] = None):
        self.path = Path(path) if path is not None else default_lake_writer_path()

    def _load_raw(self) -> Dict[str, Any]:
        if not self.path.is_file():
            return {"version": 2, "users": {}}
        try:
            with open(self.path, "r", encoding="utf-8") as f:
                raw = json.load(f)
        except Exception as exc:
            logger.error("读取拉科写作会话文件失败: %s", exc)
            return {"version": 2, "users": {}}

        if isinstance(raw, dict) and raw.get("version") == 2 and isinstance(raw.get("users"), dict):
            return raw

        # 自动兼容迁移 v1 扁平格式 {"lake_writer::<user>": [messages...]}
        migrated: Dict[str, Any] = {"version": 2, "users": {}}
        if isinstance(raw, dict):
            for key, val in raw.items():
                if not isinstance(val, list):
                    continue
                owner = key.split("::", 1)[1] if "::" in key else key
                owner = (owner or "default").strip() or "default"
                title = "历史文章"
                if val:
                    first_user = next((m.get("content", "") for m in val if m.get("role") == "user"), "")
                    title = _auto_title_from_prompt(first_user, "历史文章")
                tab = _make_tab(owner, title=title, auto_titled=False, messages=val)
                migrated["users"][owner] = {
                    "active_tab_id": tab["id"],
                    "tabs": [tab],
                }
        return migrated

    def _save_raw(self, data: Dict[str, Any]) -> None:
        self.path.parent.mkdir(parents=True, exist_ok=True)
        tmp_path = self.path.with_suffix(".json.tmp")
        with open(tmp_path, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)
        os.replace(tmp_path, self.path)

    @staticmethod
    def _ensure_user_bucket(data: Dict[str, Any], owner: str) -> Dict[str, Any]:
        owner_key = (owner or "default").strip() or "default"
        users = data.setdefault("users", {})
        bucket = users.get(owner_key)
        if not isinstance(bucket, dict):
            bucket = {"active_tab_id": None, "tabs": []}
            users[owner_key] = bucket
        tabs = bucket.setdefault("tabs", [])
        active_tabs = [t for t in tabs if t.get("status", "active") == "active"]
        if not active_tabs:
            tab = _make_tab(owner_key, title="新文章 1", auto_titled=True)
            tabs.append(tab)
            bucket["active_tab_id"] = tab["id"]
        else:
            active_ids = {t["id"] for t in active_tabs}
            if bucket.get("active_tab_id") not in active_ids:
                bucket["active_tab_id"] = active_tabs[-1]["id"]
        return bucket

    @staticmethod
    def _tab_summary_for_user(tab: Dict[str, Any]) -> Dict[str, Any]:
        msgs = tab.get("messages") or []
        return {
            "id": tab["id"],
            "title": tab.get("title") or "未命名文章",
            "message_count": len(msgs),
            "created_at": tab.get("created_at") or "",
            "updated_at": tab.get("updated_at") or "",
        }

    def list_user_tabs(self, owner: str, tab_id: Optional[str] = None) -> Dict[str, Any]:
        """返回用户可见的活跃标签列表与指定（或当前激活）标签的活跃消息。"""
        with _STORE_LOCK:
            data = self._load_raw()
            bucket = self._ensure_user_bucket(data, owner)
            active_tabs = [t for t in bucket["tabs"] if t.get("status", "active") == "active"]
            target = None
            if tab_id:
                target = next((t for t in active_tabs if t["id"] == tab_id), None)
            if target is None:
                target = next((t for t in active_tabs if t["id"] == bucket.get("active_tab_id")), active_tabs[-1])
            bucket["active_tab_id"] = target["id"]
            self._save_raw(data)
            return {
                "active_tab_id": target["id"],
                "tabs": [self._tab_summary_for_user(t) for t in active_tabs],
                "messages": list(target.get("messages") or []),
            }

    def create_tab(self, owner: str, title: Optional[str] = None) -> Dict[str, Any]:
        """新建一个独立空白对话标签并设为当前激活标签。"""
        with _STORE_LOCK:
            data = self._load_raw()
            bucket = self._ensure_user_bucket(data, owner)
            clean_title = (title or "").strip()
            auto_titled = not bool(clean_title)
            final_title = clean_title[:40] if clean_title else _next_default_title(bucket["tabs"])
            tab = _make_tab(owner, title=final_title, auto_titled=auto_titled)
            bucket["tabs"].append(tab)
            bucket["active_tab_id"] = tab["id"]
            self._save_raw(data)
            active_tabs = [t for t in bucket["tabs"] if t.get("status", "active") == "active"]
            return {
                "tab": self._tab_summary_for_user(tab),
                "active_tab_id": tab["id"],
                "tabs": [self._tab_summary_for_user(t) for t in active_tabs],
                "messages": [],
            }

    def rename_tab(self, owner: str, tab_id: str, title: str) -> Optional[Dict[str, Any]]:
        """重命名用户的活跃标签。"""
        clean_title = (title or "").strip()[:40]
        if not clean_title:
            return None
        with _STORE_LOCK:
            data = self._load_raw()
            bucket = self._ensure_user_bucket(data, owner)
            tab = next(
                (t for t in bucket["tabs"] if t["id"] == tab_id and t.get("status", "active") == "active"),
                None,
            )
            if tab is None:
                return None
            tab["title"] = clean_title
            tab["auto_titled"] = False
            tab["updated_at"] = _now_iso()
            self._save_raw(data)
            active_tabs = [t for t in bucket["tabs"] if t.get("status", "active") == "active"]
            return {
                "tab": self._tab_summary_for_user(tab),
                "active_tab_id": bucket["active_tab_id"],
                "tabs": [self._tab_summary_for_user(t) for t in active_tabs],
            }

    def soft_clear_tab(self, owner: str, tab_id: Optional[str] = None) -> Dict[str, Any]:
        """软清空指定标签对话：不物理删除，将当前消息完整归档到 archived_batches 后置空。"""
        with _STORE_LOCK:
            data = self._load_raw()
            bucket = self._ensure_user_bucket(data, owner)
            active_tabs = [t for t in bucket["tabs"] if t.get("status", "active") == "active"]
            target = None
            if tab_id:
                target = next((t for t in active_tabs if t["id"] == tab_id), None)
            if target is None:
                target = next((t for t in active_tabs if t["id"] == bucket.get("active_tab_id")), active_tabs[-1])

            archived_count = 0
            existing_msgs = list(target.get("messages") or [])
            if existing_msgs:
                batches = target.setdefault("archived_batches", [])
                batches.append({
                    "id": _new_id("arch"),
                    "cleared_at": _now_iso(),
                    "reason": "cleared",
                    "messages": existing_msgs,
                })
                archived_count = len(existing_msgs)
                target["messages"] = []
                target["updated_at"] = _now_iso()

            self._save_raw(data)
            return {
                "tab_id": target["id"],
                "archived_count": archived_count,
                "active_tab_id": bucket["active_tab_id"],
                "tabs": [self._tab_summary_for_user(t) for t in active_tabs],
                "messages": [],
            }

    def soft_delete_tab(self, owner: str, tab_id: str) -> Dict[str, Any]:
        """软删除（关闭）指定标签：保留全部活跃消息和清空归档，仅对普通用户隐藏。"""
        with _STORE_LOCK:
            data = self._load_raw()
            bucket = self._ensure_user_bucket(data, owner)
            target = next(
                (t for t in bucket["tabs"] if t["id"] == tab_id and t.get("status", "active") == "active"),
                None,
            )
            if target is not None:
                target["status"] = "deleted"
                target["deleted_at"] = _now_iso()
                target["updated_at"] = _now_iso()

            # 若全部活跃标签都被关闭，自动补充一个新的空白标签
            bucket = self._ensure_user_bucket(data, owner)
            active_tabs = [t for t in bucket["tabs"] if t.get("status", "active") == "active"]
            if bucket.get("active_tab_id") == tab_id or bucket.get("active_tab_id") not in {t["id"] for t in active_tabs}:
                bucket["active_tab_id"] = active_tabs[-1]["id"]
            current = next((t for t in active_tabs if t["id"] == bucket["active_tab_id"]), active_tabs[-1])
            self._save_raw(data)
            return {
                "deleted_tab_id": tab_id,
                "active_tab_id": current["id"],
                "tabs": [self._tab_summary_for_user(t) for t in active_tabs],
                "messages": list(current.get("messages") or []),
            }

    def get_tab_context_messages(self, owner: str, tab_id: Optional[str] = None) -> tuple[str, List[Dict[str, Any]]]:
        """获取指定标签的 ID 和当前活跃消息列表（仅用于发给 Gemini，不含已清空归档）。"""
        with _STORE_LOCK:
            data = self._load_raw()
            bucket = self._ensure_user_bucket(data, owner)
            active_tabs = [t for t in bucket["tabs"] if t.get("status", "active") == "active"]
            target = None
            if tab_id:
                target = next((t for t in active_tabs if t["id"] == tab_id), None)
            if target is None:
                target = next((t for t in active_tabs if t["id"] == bucket.get("active_tab_id")), active_tabs[-1])
            return target["id"], list(target.get("messages") or [])

    def append_exchange(
        self,
        owner: str,
        tab_id: Optional[str],
        prompt: str,
        answer: str,
        model: str = "",
    ) -> Dict[str, Any]:
        """追加本轮问答到指定标签，必要时自动根据首句生成标签标题。"""
        with _STORE_LOCK:
            data = self._load_raw()
            bucket = self._ensure_user_bucket(data, owner)
            active_tabs = [t for t in bucket["tabs"] if t.get("status", "active") == "active"]
            target = None
            if tab_id:
                target = next((t for t in active_tabs if t["id"] == tab_id), None)
            if target is None:
                target = next((t for t in active_tabs if t["id"] == bucket.get("active_tab_id")), active_tabs[-1])

            msgs = list(target.get("messages") or [])
            had_no_messages = len(msgs) == 0
            now_iso = _now_iso()
            now_hms = _now_hms()
            msgs.append({"role": "user", "content": prompt, "time_str": now_hms, "time": now_iso})
            msgs.append({
                "role": "assistant",
                "content": answer,
                "time_str": now_hms,
                "time": now_iso,
                "model": model or "",
            })

            # 若超过单标签上限，溢出部分也归档保存而不是丢弃
            if len(msgs) > MAX_ACTIVE_MESSAGES_PER_TAB:
                overflow = msgs[:-MAX_ACTIVE_MESSAGES_PER_TAB]
                msgs = msgs[-MAX_ACTIVE_MESSAGES_PER_TAB:]
                target.setdefault("archived_batches", []).append({
                    "id": _new_id("arch"),
                    "cleared_at": now_iso,
                    "reason": "rotated",
                    "messages": overflow,
                })

            target["messages"] = msgs
            target["updated_at"] = now_iso
            if had_no_messages and target.get("auto_titled", False):
                target["title"] = _auto_title_from_prompt(prompt, target.get("title") or "新文章")
            bucket["active_tab_id"] = target["id"]
            self._save_raw(data)

            return {
                "tab": self._tab_summary_for_user(target),
                "active_tab_id": target["id"],
                "tabs": [self._tab_summary_for_user(t) for t in active_tabs],
            }

    def list_all_archives(
        self,
        owner_filter: Optional[str] = None,
        keyword: Optional[str] = None,
    ) -> Dict[str, Any]:
        """管理员 / Mac 客户端专用：读取全部账号的活跃、已删除及已清空归档对话记录。"""
        with _STORE_LOCK:
            data = self._load_raw()
            users_map = data.get("users") or {}
            owners = sorted(users_map.keys())
            kw = (keyword or "").strip().lower()
            target_owner = (owner_filter or "").strip()

            records: List[Dict[str, Any]] = []
            for owner_name, bucket in users_map.items():
                if target_owner and owner_name != target_owner:
                    continue
                for tab in bucket.get("tabs") or []:
                    active_msgs = list(tab.get("messages") or [])
                    batches = list(tab.get("archived_batches") or [])
                    archived_msg_count = sum(len(b.get("messages") or []) for b in batches)
                    total_msgs = len(active_msgs) + archived_msg_count

                    if kw:
                        haystack_parts = [owner_name, str(tab.get("title") or "")]
                        haystack_parts.extend(str(m.get("content") or "") for m in active_msgs)
                        for b in batches:
                            haystack_parts.extend(str(m.get("content") or "") for m in (b.get("messages") or []))
                        if kw not in "\n".join(haystack_parts).lower():
                            continue

                    records.append({
                        "id": tab.get("id") or "",
                        "owner": owner_name,
                        "title": tab.get("title") or "未命名文章",
                        "status": tab.get("status") or "active",
                        "created_at": tab.get("created_at") or "",
                        "updated_at": tab.get("updated_at") or "",
                        "deleted_at": tab.get("deleted_at"),
                        "active_message_count": len(active_msgs),
                        "archived_batch_count": len(batches),
                        "archived_message_count": archived_msg_count,
                        "total_message_count": total_msgs,
                        "messages": active_msgs,
                        "archived_batches": batches,
                    })

            # 按最近更新时间倒序排列，优先展示「拉科」的记录或最新活跃记录
            records.sort(key=lambda item: (item.get("updated_at") or "", item.get("created_at") or ""), reverse=True)
            return {
                "owners": owners,
                "total_tabs": len(records),
                "records": records,
            }
