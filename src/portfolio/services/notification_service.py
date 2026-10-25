"""macOS system desktop notifications and alert sound delivery."""

import logging
import os
import subprocess
import threading
from pathlib import Path
from typing import Optional

logger = logging.getLogger(__name__)


class NotificationService:
    def __init__(self):
        self.muted: bool = False
        self._sound_cache = {}
        self.bark_url: Optional[str] = None
        self._load_bark_config()

    def _load_bark_config(self):
        """Load Bark mobile push endpoint from api_config.json if configured."""
        try:
            from portfolio.paths import API_CONFIG_PATH
            import json
            if API_CONFIG_PATH.is_file():
                with open(API_CONFIG_PATH, "r", encoding="utf-8") as f:
                    cfg = json.load(f)
                    bark_cfg = cfg.get("bark", {})
                    if bark_cfg.get("enabled", True) and bark_cfg.get("url"):
                        self.bark_url = bark_cfg.get("url").strip()
        except Exception:
            pass

    def set_bark_url(self, url: Optional[str]):
        self.bark_url = url.strip() if url else None

    def set_muted(self, muted: bool):
        self.muted = muted

    def send_bark_notification(
        self,
        title: str,
        message: str,
        sound: Optional[str] = "bell.caf",
        group: str = "Money3行情监控",
        url: Optional[str] = None,
    ):
        """
        Deliver native push notification to user's iPhone via Bark API.
        Runs asynchronously in a background daemon thread.
        """
        if self.muted or not self.bark_url:
            return

        def _push():
            try:
                import requests
                endpoint = self.bark_url.rstrip("/")
                payload = {
                    "title": title,
                    "body": message,
                    "group": group,
                    "sound": sound or "bell.caf",
                    "badge": 1,
                    "level": "timeSensitive",
                }
                if url:
                    payload["url"] = url
                requests.post(endpoint, json=payload, timeout=5)
            except Exception as e:
                logger.warning(f"iPhone Bark 推送发送失败: {e}")

        threading.Thread(target=_push, daemon=True, name="money3-bark-notify").start()

    def send_desktop_notification(
        self,
        title: str,
        message: str,
        sound_name: Optional[str] = "Glass",
    ):
        """
        Deliver a native macOS notification using AppleScript asynchronously.
        Does not block the calling or UI thread.
        """
        if self.muted:
            return

        def _run():
            try:
                # Sanitize single and double quotes for AppleScript string literals
                clean_title = (
                    title.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").strip()
                )
                clean_msg = (
                    message.replace("\\", "\\\\").replace('"', '\\"').replace("\n", " ").strip()
                )

                script = f'display notification "{clean_msg}" with title "{clean_title}"'
                if sound_name:
                    clean_sound = sound_name.replace('"', "")
                    script += f' sound name "{clean_sound}"'

                subprocess.run(
                    ["osascript", "-e", script],
                    capture_output=True,
                    timeout=5,
                    check=False,
                )
            except Exception as e:
                logger.warning(f"桌面通知发送失败: {e}")

        threading.Thread(target=_run, daemon=True, name="money3-desktop-notify").start()

    def play_sound(self, sound_name: str = "Glass"):
        """Play macOS system audio in background thread."""
        if self.muted or not sound_name:
            return

        def _play():
            sound_path = Path(f"/System/Library/Sounds/{sound_name}.aiff")
            if sound_path.exists():
                try:
                    subprocess.run(["afplay", str(sound_path)], capture_output=True, timeout=5)
                except Exception:
                    pass

        threading.Thread(target=_play, daemon=True, name="money3-sound-play").start()


    def notify_event(self, event, channels: str = "in_app,desktop", sound_name: Optional[str] = "Glass"):
        """Deliver alert event via configured channels (desktop notification, sound, and iPhone Bark push)."""
        if self.muted:
            return
        ch = [c.strip().lower() for c in (channels or "").split(",")]
        title = f"【行情预警】{event.analysis_symbol} 触发提醒"
        msg = f"{event.rule_name}\n现价: ${event.observed_price} | {event.trigger_reason}"
        if "desktop" in ch:
            self.send_desktop_notification(title, msg, sound_name=sound_name if "sound" in ch else None)
        elif "sound" in ch:
            self.play_sound(sound_name or "Glass")

        # iPhone Bark 实时推送
        if "bark" in ch or self.bark_url:
            self.send_bark_notification(title, msg, sound="alarm.caf")


# Global singleton instance
notification_service = NotificationService()
