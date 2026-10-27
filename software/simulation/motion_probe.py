#!/usr/bin/env python3
"""motion_probe.py — 量化 .motion 在仿真里的可视运动量（诊断"优美不优美"）。

报告身体中心的高度区间、水平漂移（侧倾/俯仰是否真的带动身体）、倾角区间。
水平漂移太小 = 观众看不出在动；配合 motion_player 的摔跤判定一起看。

用法：python motion_probe.py <motion.motion> [<more.motion> ...]
"""
from __future__ import annotations

import math
import sys
from pathlib import Path

import mujoco
import numpy as np

sys.path.insert(0, str(Path(__file__).resolve().parent))
from motion_player import load_motion, sample_motion  # noqa: E402
from sim_load import load_model, JOINT_ORDER  # noqa: E402


def probe(motion: dict) -> dict:
    model = load_model()
    data = mujoco.MjData(model)
    act_ids = [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, f"{n}_servo")
               for n in JOINT_ORDER]
    base = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "base")

    hs, tilt, xy = [], [], []
    while data.time < motion["duration_ms"] / 1000.0:
        for act, a in zip(act_ids, sample_motion(motion, data.time * 1000.0)):
            data.ctrl[act] = math.radians(a)
        mujoco.mj_step(model, data)
        p = data.xpos[base]
        hs.append(float(p[2]))
        xy.append((float(p[0]), float(p[1])))
        tilt.append(math.degrees(math.acos(float(np.clip(data.xmat[base][8], -1, 1)))))

    h0 = np.mean(hs[:20])                       # 起始稳态高度
    sway = max(math.dist(a, b) for a in xy for b in xy) * 1000
    return {
        "motion": motion.get("name"),
        "height_mm": f"{(min(hs) - h0) * 1000:+.1f}~{(max(hs) - h0) * 1000:+.1f}",
        "sway_mm": round(sway, 1),
        "tilt_deg": f"{min(tilt):.1f}~{max(tilt):.1f}",
    }


def main() -> int:
    rows = [probe(load_motion(Path(a))) for a in sys.argv[1:]]
    print(f"{'motion':<12} {'heightΔ(mm)':>14} {'sway(mm)':>9} {'tilt(°)':>10}")
    for r in rows:
        print(f"{r['motion']:<12} {r['height_mm']:>14} {r['sway_mm']:>9} {r['tilt_deg']:>10}")
    return 0


if __name__ == "__main__":
    sys.exit(main())
