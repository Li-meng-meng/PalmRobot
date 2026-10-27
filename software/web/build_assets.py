#!/usr/bin/env python3
"""生成 web/assets/robot-assets.js（MJCF + STL base64 + 样例动作包内嵌）。

目的：让 index.html 在 file:// 协议下零服务双击可用（浏览器禁止 file:// fetch）。
assets 更新后重跑：python build_assets.py
"""
import base64
import json
import pathlib
import sys

WEB = pathlib.Path(__file__).resolve().parent
ROOT = WEB.parent
MJCF = ROOT / "simulation" / "assets" / "mjcf" / "sesame.xml"
MESHES = ROOT / "simulation" / "assets" / "meshes"
MOTIONS = ROOT / "tools" / "motions"
OUT = WEB / "assets" / "robot-assets.js"


def main() -> int:
    if not MJCF.exists():
        print(f"缺少 {MJCF}", file=sys.stderr)
        return 1

    payload: dict = {"mjcf": MJCF.read_text(encoding="utf-8"), "meshes": {}, "motions": {}}

    for stl in sorted(MESHES.glob("*.stl")):
        payload["meshes"][stl.name] = base64.b64encode(stl.read_bytes()).decode("ascii")

    if MOTIONS.exists():
        for m in sorted(MOTIONS.glob("*.motion")):
            payload["motions"][m.stem] = m.read_text(encoding="utf-8")

    OUT.parent.mkdir(parents=True, exist_ok=True)
    OUT.write_text(
        "/* 自动生成：python build_assets.py。勿手改。 */\n"
        "window.SESAME_ASSETS = " + json.dumps(payload) + ";\n",
        encoding="utf-8",
    )
    print(f"OK {OUT} ({OUT.stat().st_size / 1024:.0f} KB, "
          f"{len(payload['meshes'])} meshes, {len(payload['motions'])} motions)")
    return 0


if __name__ == "__main__":
    sys.exit(main())
