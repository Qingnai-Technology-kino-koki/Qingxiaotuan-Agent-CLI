"""纯 Python 实现 notify 引擎 (替代 ext/ts/src/notify/main.ts)
跨平台桌面通知
"""
import json
import sys
import platform
import subprocess
import os


class NotifyEngine:
    """统一 JSONL IPC 协议的 notify 引擎"""

    def __init__(self):
        self.methods = {
            "notify": self.notify,
            "_meta/list": self.list_methods,
        }

    def list_methods(self, params=None):
        return {
            "engine": "notify",
            "version": "1.0.0-python",
            "methods": list(self.methods.keys()),
            "capabilities": ["desktop_notification", "cross_platform"],
        }

    def notify(self, params):
        """发送桌面通知"""
        title = params.get("title", "Qingxiaotuan")
        message = params.get("message", "")
        system = platform.system()

        try:
            if system == "Windows":
                # PowerShell toast
                script = (
                    "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null;"
                    "$template = [Windows.UI.Notifications.ToastTemplateType]::ToastText02;"
                    "$toast = [Windows.UI.Notifications.ToastNotification]::new($template);"
                    "$toast.Content.GetXmlDocument().GetElementsByTagName('text')[0].InnerText = '" + title.replace("'", "''") + "';"
                    "$toast.Content.GetXmlDocument().GetElementsByTagName('text')[1].InnerText = '" + message.replace("'", "''") + "';"
                    "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('qxt').Show($toast)"
                )
                subprocess.run(["powershell", "-NoProfile", "-Command", script], capture_output=True, timeout=5)
            elif system == "Darwin":
                script = f'osascript -e \'display notification "{message}" with title "{title}"\''
                subprocess.run(script, shell=True, capture_output=True, timeout=5)
            else:
                # Linux notify-send
                subprocess.run(["notify-send", title, message], capture_output=True, timeout=5)
            return {"sent": True, "system": system}
        except Exception as e:
            return {"sent": False, "error": str(e), "system": system}

    def handle(self, line):
        try:
            req = json.loads(line)
            method = req.get("method", "")
            params = req.get("params", {})
            req_id = req.get("id", None)
            if method in self.methods:
                result = self.methods[method](params)
                resp = {"id": req_id, "ok": True, "result": result}
            else:
                resp = {"id": req_id, "ok": False, "error": f"Unknown method: {method}"}
            return json.dumps(resp, ensure_ascii=False)
        except Exception as e:
            resp = {"id": None, "ok": False, "error": str(e)}
            return json.dumps(resp, ensure_ascii=False)

    def run(self):
        sys.stdout.write(json.dumps({"ready": True}) + "\n")
        sys.stdout.flush()
        for line in sys.stdin:
            line = line.strip()
            if not line:
                continue
            sys.stdout.write(self.handle(line) + "\n")
            sys.stdout.flush()


if __name__ == "__main__":
    NotifyEngine().run()
