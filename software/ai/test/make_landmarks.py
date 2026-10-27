#!/usr/bin/env python3
"""make_landmarks.py — 合成 MediaPipe Pose 关键点序列（L2 重定向的测试输入）。

模拟一段人体动作：左右移步 → 蹲起 → 右手举 → 左手举。
输出与 pose_to_motion.py --export 同格式（可互换），用于无真人视频时验证重定向链路。

用法：python make_landmarks.py [out.json] [n_frames]
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

# 只需要填 pose_to_motion.py 用到的 9 个点，其余置画面中心
IDX = {"nose": 0, "r_sho": 12, "l_sho": 11, "r_wrist": 16, "l_wrist": 15,
       "r_hip": 24, "l_hip": 23}


def make_frame(t: float, n: int) -> list:
    p = [{"x": 0.5, "y": 0.5, "visibility": 1.0} for _ in range(33)]

    def set(k, x, y):
        p[IDX[k]] = {"x": x, "y": y, "visibility": 1.0}

    phase1, phase2 = n * 0.33, n * 0.66
    if t < phase1:                       # 左右移步
        dx = 0.15 * math.sin(2 * math.pi * t / (phase1 / 2))
    else:
        dx = 0.0
    dy = 0.1 * max(0.0, math.sin(math.pi * (t - phase1) / (phase2 - phase1))) \
        if phase1 <= t < phase2 else 0.0   # 蹲起（y 向下为正）
    r_up = phase2 <= t < phase2 + (n - phase2) / 2   # 前段右手举
    l_up = t >= phase2 + (n - phase2) / 2            # 后段左手举

    set("r_sho", 0.45, 0.35)
    set("l_sho", 0.55, 0.35)
    set("r_wrist", 0.45, 0.30 if r_up else 0.55)     # 举手 = y 小于肩 0.35
    set("l_wrist", 0.55, 0.30 if l_up else 0.55)
    set("r_hip", 0.47 + dx, 0.60 + dy)
    set("l_hip", 0.53 + dx, 0.60 + dy)
    return p


def main() -> int:
    out = Path(sys.argv[1]) if len(sys.argv) > 1 else Path(__file__).parent / "pose_test.json"
    n = int(sys.argv[2]) if len(sys.argv) > 2 else 300
    payload = {"fps": 30.0, "frames": [make_frame(i, n) for i in range(n)]}
    out.write_text(json.dumps(payload), encoding="utf-8")
    print(f"已生成 {out}（{n} 帧合成关键点：移步→蹲起→右手举→左手举）")
    return 0


if __name__ == "__main__":
    sys.exit(main())
