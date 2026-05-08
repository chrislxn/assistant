#!/usr/bin/env python3
"""
claude-runner: HOST 侧 HTTP 服务，接受 POST /run 调用 claude CLI。
供 telegram-bot Docker 容器通过 172.18.0.1:7777 调用。
"""
import http.server
import json
import logging
import os
import subprocess
from pathlib import Path

WORK_DIR = "/home/chris/assistant"
PORT = 7777

logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(message)s")
log = logging.getLogger(__name__)


def _load_env_file() -> dict:
    result = {}
    try:
        for line in (Path(WORK_DIR) / ".env").read_text().splitlines():
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, _, v = line.partition("=")
                result[k.strip()] = v.strip()
    except Exception as e:
        log.warning("读取 .env 失败: %s", e)
    return result


_env_vars: dict = {}
_token: str = ""


class RunnerHandler(http.server.BaseHTTPRequestHandler):
    def do_POST(self):
        if self.path != "/run":
            self.send_response(404)
            self.end_headers()
            return

        if _token and self.headers.get("Authorization") != f"Bearer {_token}":
            self._resp(401, {"error": "unauthorized"})
            return

        length = int(self.headers.get("Content-Length", 0))
        try:
            body = json.loads(self.rfile.read(length))
        except Exception:
            self._resp(400, {"error": "invalid json"})
            return

        task = body.get("task", "").strip()
        if not task:
            self._resp(400, {"error": "task required"})
            return

        log.info("任务收到（%d 字符）: %s…", len(task), task[:100])

        env = {
            **os.environ,
            "ANTHROPIC_BASE_URL": "https://co.yes.vg",
            "ANTHROPIC_API_KEY": _env_vars.get("API_KEY", ""),
        }

        try:
            result = subprocess.run(
                ["claude", "-p", task, "--dangerously-skip-permissions"],
                cwd=WORK_DIR,
                capture_output=True,
                text=True,
                timeout=300,
                env=env,
            )
            output = result.stdout.strip() or result.stderr.strip()
            log.info("完成（rc=%d）: %s…", result.returncode, output[:100])
            self._resp(200, {"output": output, "returncode": result.returncode})
        except subprocess.TimeoutExpired:
            log.warning("超时（300s）")
            self._resp(200, {"output": "", "error": "timeout（300s）"})
        except Exception as e:
            log.error("执行异常: %s", e)
            self._resp(500, {"output": "", "error": str(e)})

    def _resp(self, code: int, data: dict) -> None:
        body = json.dumps(data, ensure_ascii=False).encode()
        self.send_response(code)
        self.send_header("Content-Type", "application/json; charset=utf-8")
        self.send_header("Content-Length", len(body))
        self.end_headers()
        self.wfile.write(body)

    def log_message(self, fmt, *args):
        pass  # 用 logging 代替 BaseHTTPServer 默认输出


if __name__ == "__main__":
    _env_vars = _load_env_file()
    _token = _env_vars.get("ACCESS_TOKEN", "")
    log.info("claude-runner 启动 0.0.0.0:%d  TOKEN=%s", PORT, "已设置" if _token else "未设置")
    http.server.HTTPServer(("0.0.0.0", PORT), RunnerHandler).serve_forever()
