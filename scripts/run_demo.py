"""Launch a safe local demonstration with a separate, disposable business database."""
from pathlib import Path
import argparse
import secrets
import subprocess
import sys
import os
from scripts.load_test import prepare_database, isolated_env, ROOT


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", type=int, default=8001)
    args = parser.parse_args()
    if not 1024 <= args.port <= 65535:
        parser.error("port must be 1024..65535")
    folder = ROOT / "reports/demo"
    folder.mkdir(parents=True, exist_ok=True)
    path = folder / ("demo-" + secrets.token_hex(8) + ".db")
    prepare_database(path)
    print(f"演示页面：http://127.0.0.1:{args.port}/demo", flush=True)
    print("使用本地客服令牌；模型采用离线证据回复，业务数据为模拟数据。", flush=True)
    print("人工标签审核：http://127.0.0.1:"+str(args.port)+"/review", flush=True)
    print("按 Ctrl+C 停止。此演示数据库不会修改原业务数据库。", flush=True)
    env = isolated_env(path, secrets.token_urlsafe(48))
    process = subprocess.Popen([sys.executable, "-m", "uvicorn", "app.main:app", "--host", "127.0.0.1", "--port", str(args.port)],
                               cwd=ROOT, env=env, creationflags=subprocess.CREATE_NO_WINDOW if os.name == "nt" else 0)
    try:
        process.wait()
    except KeyboardInterrupt:
        process.terminate()
        try: process.wait(timeout=10)
        except subprocess.TimeoutExpired: process.kill(); process.wait(timeout=5)


if __name__ == "__main__":
    main()
