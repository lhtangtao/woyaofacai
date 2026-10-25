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

    def set_muted(self, muted: bool):
        self.muted = muted

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
        """Deliver alert event via configured channels (desktop notification and sound)."""
        if self.muted:
            return
        ch = [c.strip().lower() for c in (channels or "").split(",")]
        title = f"【行情预警】{event.analysis_symbol} 触发提醒"
        msg = f"{event.rule_name}\n现价: {event.observed_price} | {event.trigger_reason}"
        if "desktop" in ch:
            self.send_desktop_notification(title, msg, sound_name=sound_name if "sound" in ch else None)
        elif "sound" in ch:
            self.play_sound(sound_name or "Glass")


# Global singleton instance
notification_service = NotificationService()
