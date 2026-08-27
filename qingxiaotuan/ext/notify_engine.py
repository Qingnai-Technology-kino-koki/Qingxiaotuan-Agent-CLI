"""纯 Python 实现 notify 引擎
跨平台桌面通知
Windows: Toast 优先, 失败自动回退气球通知; 附带 beep 与 dry_run
"""
import json
import sys
import platform
import subprocess


def _run_cmd(cmd, timeout=5):
    """执行命令, 返回 (returncode, stderr 片段)。"""
    try:
        proc = subprocess.run(cmd, capture_output=True, timeout=timeout)
        return proc.returncode, (proc.stderr or b"")[-200:].decode(errors="replace").strip()
    except Exception as e:
        return -1, str(e)[:200]


class NotifyEngine:
    """统一 JSONL IPC 协议的 notify 引擎"""

    def __init__(self):
        self.methods = {
            "notify": self.notify,
            "beep": self.beep,
            "_meta/list": self.list_methods,
        }

    def list_methods(self, params=None):
        return {
            "engine": "notify",
            "version": "1.1.0-python",
            "methods": list(self.methods.keys()),
            "capabilities": ["desktop_notification", "cross_platform",
                             "windows_fallback", "beep", "dry_run"],
        }

    def _backend(self, system):
        """报告平台将使用的通知后端名 (供 dry_run 与结果标注)。"""
        if system == "Windows":
            return "toast"
        if system == "Darwin":
            return "osascript"
        return "notify-send"

    @staticmethod
    def _toast_script():
        return (
            "[Windows.UI.Notifications.ToastNotificationManager, Windows.UI.Notifications, ContentType=WindowsRuntime] | Out-Null;"
            "$template = [Windows.UI.Notifications.ToastTemplateType]::ToastText02;"
            "$toast = [Windows.UI.Notifications.ToastNotification]::new($template);"
            "$toast.Content.GetXmlDocument().GetElementsByTagName('text')[0].InnerText = '{title}';"
            "$toast.Content.GetXmlDocument().GetElementsByTagName('text')[1].InnerText = '{message}';"
            "[Windows.UI.Notifications.ToastNotificationManager]::CreateToastNotifier('qxt').Show($toast)"
        )

    def _send(self, title, message, system):
        """按平台发送, 返回 {sent, backend, error?, fallback_used?}。"""
        if system == "Windows":
            # 先转义单引号再填充模板
            script = self._toast_script().replace(
                "{title}", title.replace("'", "''")).replace(
                "{message}", message.replace("'", "''"))
            code, err = _run_cmd(["powershell", "-NoProfile", "-Command", script])
            if code == 0:
                return {"sent": True, "backend": "toast"}
            # 回退: WinForms 气球通知
            esc_title = title.replace("'", "''")
            esc_message = message.replace("'", "''")
            fallback = (
                "Add-Type -AssemblyName System.Windows.Forms;"
                "Add-Type -AssemblyName System.Drawing;"
                "$n = New-Object System.Windows.Forms.NotifyIcon;"
                "$n.Icon = [System.Drawing.SystemIcons]::Information;"
                "$n.Visible = $true;"
                f"$n.ShowBalloonTip(5000, '{esc_title}', '{esc_message}', 'Info');"
                "Start-Sleep -Seconds 6; $n.Dispose()"
            )
            code2, err2 = _run_cmd(["powershell", "-NoProfile", "-Command", fallback])
            if code2 == 0:
                return {"sent": True, "backend": "balloon", "fallback_used": True,
                        "first_error": err}
            return {"sent": False, "error": err2 or err, "system": system}
        if system == "Darwin":
            script = ('osascript -e \'display notification "{m}" with title "{t}"\''
                      .replace("{m}", message).replace("{t}", title))
            code, err = _run_cmd(script, timeout=5)
            return {"sent": code == 0, "backend": "osascript",
                    **({} if code == 0 else {"error": err})}
        code, err = _run_cmd(["notify-send", title, message])
        return {"sent": code == 0, "backend": "notify-send",
                **({} if code == 0 else {"error": err})}

    def notify(self, params):
        """发送桌面通知; params.dry_run=true 时只报告将使用的后端, 不实际发送"""
        title = str(params.get("title", "Qingxiaotuan"))[:200]
        message = str(params.get("message", ""))[:500]
        system = platform.system()

        if params.get("dry_run"):
            return {"sent": False, "dry_run": True, "system": system,
                    "backend": self._backend(system)}

        try:
            result = self._send(title, message, system)
            result.setdefault("system", system)
            return result
        except Exception as e:
            return {"sent": False, "error": str(e), "system": system}

    def beep(self, params):
        """终端提示音; dry_run 只报告方式不响铃 (stdout 是 IPC 协议通道, 铃声走 stderr)"""
        system = platform.system()
        if params.get("dry_run"):
            way = "winsound.MessageBeep" if system == "Windows" else "stderr bell"
            return {"played": False, "dry_run": True, "way": way}
        try:
            if system == "Windows":
                import winsound
                winsound.MessageBeep()
            else:
                sys.stderr.write("\a")
                sys.stderr.flush()
            return {"played": True, "way": "beep"}
        except Exception as e:
            return {"played": False, "error": str(e)}

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
