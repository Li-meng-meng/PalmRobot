#!/usr/bin/env python3
"""sim_load.py — T2：加载 sesame MJCF 模型，验证仿真环境可用（P0）

验证内容：
  1. MJCF + STL 网格能加载（质量、惯量、执行器齐全）
  2. 站立位（stand_degrees）下 2 秒物理仿真保持稳定
  3. 四脚触地、身体高度与倾角在阈值内

用法：
  .venv/Scripts/python sim_load.py            # 无头验证
  .venv/Scripts/python sim_load.py --png out.png   # 离屏渲染一帧
  .venv/Scripts/python sim_load.py --view     # 打开交互 viewer
"""
from __future__ import annotations

import argparse
import math
import sys
from pathlib import Path

import mujoco
import numpy as np

HERE = Path(__file__).resolve().parent
MJCF = HERE / "assets" / "mjcf" / "sesame.xml"
MESH_DIR = HERE / "assets" / "meshes"

# 与 assets/calibration/default.yaml 一致（勿单方面修改）
JOINT_ORDER = ["R1", "R2", "L1", "L2", "R4", "R3", "L3", "L4"]
STAND_DEG = [135, 45, 45, 135, 0, 180, 0, 180]


def load_model() -> mujoco.MjModel:
    """加载 MJCF。

    坑（已验证）：① Windows 下 MuJoCo 的 C 层无法打开含中文的路径；
    ② 本仓库 MJCF 与 STL 分属 mjcf/ 与 meshes/ 两个目录且 XML 未写 meshdir。
    因此统一走 from_xml_string + assets 字典，两个问题一起解决。
    """
    xml = MJCF.read_text(encoding="utf-8")
    assets = {f.name: f.read_bytes() for f in MESH_DIR.glob("*.stl")}
    return mujoco.MjModel.from_xml_string(xml, assets)

T_SIM = 2.0        # 站立验证时长（s）
MAX_TILT_DEG = 15  # 稳定判定：倾角上限
MIN_HEIGHT = 0.02  # 稳定判定：身体最低高度（m）


def stand_ctrl(model: mujoco.MjModel) -> np.ndarray:
    """站立位 ctrl（弧度）。固件角度域（0-180°）与 MJCF ctrl 一一对应。"""
    ctrl = np.zeros(model.nu)
    for i, name in enumerate(JOINT_ORDER):
        act = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, f"{name}_servo")
        ctrl[act] = math.radians(STAND_DEG[i])
    return ctrl


def run_standing(model: mujoco.MjModel, verbose: bool = True) -> dict:
    data = mujoco.MjData(model)
    data.ctrl[:] = stand_ctrl(model)
    mujoco.mj_forward(model, data)

    base_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "base")
    foot_ids = [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_SITE, f"{f}_contact")
                for f in ("r3", "r4", "l3", "l4")]

    min_h, max_tilt = 9.9, 0.0
    while data.time < T_SIM:
        mujoco.mj_step(model, data)
        h = data.xpos[base_id][2]
        r = data.xmat[base_id].reshape(3, 3)
        tilt = math.degrees(math.acos(min(1.0, abs(r[2, 2]))))
        min_h = min(min_h, h)
        max_tilt = max(max_tilt, tilt)

    # 结束时触地判定：四脚 site 世界 z 高度 < 12 mm 视为触地
    touching = 0
    for s in foot_ids:
        z = data.site_xpos[s][2]
        if z < 0.012:
            touching += 1

    result = {
        "sim_time_s": round(data.time, 3),
        "final_height_m": round(float(data.xpos[base_id][2]), 4),
        "min_height_m": round(float(min_h), 4),
        "max_tilt_deg": round(max_tilt, 2),
        "feet_on_ground": f"{touching}/4",
        "stable": bool(min_h > MIN_HEIGHT and max_tilt < MAX_TILT_DEG and touching == 4),
    }
    if verbose:
        for k, v in result.items():
            print(f"  {k:>18}: {v}")
    return result


def render_png(out: Path) -> None:
    from PIL import Image

    model = load_model()
    data = mujoco.MjData(model)
    data.ctrl[:] = stand_ctrl(model)
    for _ in range(500):
        mujoco.mj_step(model, data)
    mujoco.mj_forward(model, data)
    with mujoco.Renderer(model, height=480, width=640) as r:
        cam = mujoco.MjvCamera()
        cam.lookat[:] = [0, 0, 0.05]
        cam.distance = 0.35
        cam.azimuth = 135
        cam.elevation = -25
        r.update_scene(data, camera=cam)
        Image.fromarray(r.render()).save(out)
    print(f"已渲染 {out}")


def main() -> int:
    ap = argparse.ArgumentParser(description="sesame MJCF 加载与站立验证")
    ap.add_argument("--png", type=Path, help="离屏渲染一帧到指定 PNG")
    ap.add_argument("--view", action="store_true", help="打开交互 viewer")
    args = ap.parse_args()

    print(f"MuJoCo {mujoco.__version__}")
    print(f"加载 {MJCF}")
    model = load_model()
    print(f"  nq/nv/nu: {model.nq}/{model.nv}/{model.nu}  总质量: {sum(model.body_mass):.3f} kg")

    if args.view:
        from mujoco import viewer as mj_viewer
        data = mujoco.MjData(model)
        data.ctrl[:] = stand_ctrl(model)
        mj_viewer.launch(model, data)
        return 0

    print("站立稳定性验证（2 s）:")
    result = run_standing(model)
    if args.png:
        render_png(args.png)
    return 0 if result["stable"] else 1


if __name__ == "__main__":
    sys.exit(main())
