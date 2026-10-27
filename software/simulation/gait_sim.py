#!/usr/bin/env python3
"""gait_sim.py — T3：步态仿真与调参（P0 核心）

三个场景：
  1. stand   站立稳定（复用 sim_load 验证逻辑）
  2. march   原地踏步（对角组交替抬膝，验证身体姿态保持）
  3. walk    直线行走（对角 trot：髋摆动 + 摆动相抬膝），小网格搜索髋摆方向与幅值

产出：
  - gait_params.json   步态参数表（选定参数 + 三场景指标）→ 交嵌入式
  - walk.motion        仿真验证过的直线行走动作包（规范见 docs/21）

用法：
  .venv/Scripts/python gait_sim.py            # 全部场景 + 参数搜索
  .venv/Scripts/python gait_sim.py --quick    # 跳过搜索，用默认参数验证
"""
from __future__ import annotations

import json
import math
import sys
from pathlib import Path

import mujoco
import numpy as np

from sim_load import load_model, stand_ctrl, STAND_DEG

HERE = Path(__file__).resolve().parent

# ctrl 下标与 STAND_DEG 下标一致（MJCF 执行器顺序 = calibration joint_order）
# 0=R1前右髋 1=R2后右髋 2=L1前左髋 3=L2后左髋 4=R4后右膝 5=R3前右膝 6=L3前左膝 7=L4后左膝
IDX = {"R1": 0, "R2": 1, "L1": 2, "L2": 3, "R4": 4, "R3": 5, "L3": 6, "L4": 7}
BASE = [float(a) for a in STAND_DEG]

D2R = math.pi / 180.0


# ---------------------------------------------------------------- 步态生成

def march_ctrl(t: float, freq: float, knee_f: float, knee_r: float) -> list[float]:
    """原地踏步：对角组交替抬膝，髋不动。

    膝基准：R3 前右膝 / L4 后左膝站立 180°（折叠 = 减小）；
            L3 前左膝 / R4 后右膝站立 0°（抬起 = 增大）。
    A 组膝 = R3 + L4，B 组膝 = L3 + R4。
    """
    ch = list(BASE)
    s = math.sin(2 * math.pi * freq * t)
    lift_a = max(0.0, s)
    lift_b = max(0.0, -s)
    ch[IDX["R3"]] = BASE[IDX["R3"]] - knee_f * lift_a
    ch[IDX["L4"]] = BASE[IDX["L4"]] - knee_r * lift_a
    ch[IDX["L3"]] = BASE[IDX["L3"]] + knee_f * lift_b
    ch[IDX["R4"]] = BASE[IDX["R4"]] + knee_r * lift_b
    return ch


def trot_ctrl(t: float, freq: float, hip_amp: float, hip_mode: str,
              hip_sign: int, knee_f: float, knee_r: float,
              knee_lead_deg: float = 0.0) -> list[float]:
    """对角 trot：A = R1+L2（前右髋+后左髋），B = L1+R2。

    hip_mode: 'same'  对角组内两髋同号摆动
              'mirror' 对角组内两髋反号摆动
    hip_sign: 整体方向 ±1（搜出来哪个往前走用哪个）
    knee_lead_deg: 膝相对髋的相位提前（负值 = 提前抬起）
    膝抬起波形用 sin³（比正弦尖锐：离地时间短、支撑相长、打滑少）
    """
    ch = list(BASE)
    s = math.sin(2 * math.pi * freq * t)
    s_k = math.sin(2 * math.pi * freq * t + math.radians(knee_lead_deg))
    lift_a = max(0.0, s_k) ** 3      # A 组摆动相
    lift_b = max(0.0, -s_k) ** 3     # B 组摆动相

    if hip_mode == "same":
        dA = (hip_sign * hip_amp * s, hip_sign * hip_amp * s)
        dB = (-hip_sign * hip_amp * s, -hip_sign * hip_amp * s)
    else:  # mirror
        dA = (hip_sign * hip_amp * s, -hip_sign * hip_amp * s)
        dB = (-hip_sign * hip_amp * s, hip_sign * hip_amp * s)

    ch[IDX["R1"]] += dA[0]
    ch[IDX["L2"]] += dA[1]
    ch[IDX["L1"]] += dB[0]
    ch[IDX["R2"]] += dB[1]

    # 膝：仅摆动相抬起。A 组膝（R3/L4）站立 180° 折叠减小；B 组膝（L3/R4）站立 0° 抬起增大
    ch[IDX["R3"]] = BASE[IDX["R3"]] - knee_f * lift_a
    ch[IDX["L4"]] = BASE[IDX["L4"]] - knee_r * lift_a
    ch[IDX["L3"]] = BASE[IDX["L3"]] + knee_f * lift_b
    ch[IDX["R4"]] = BASE[IDX["R4"]] + knee_r * lift_b
    return ch


# ---------------------------------------------------------------- 仿真执行

def run(model: mujoco.MjModel, ctrl_fn, t_end: float) -> dict:
    """跑一段仿真，返回身体位移/姿态指标。"""
    data = mujoco.MjData(model)
    mujoco.mj_forward(model, data)
    base = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "base")
    x0, y0 = data.xpos[base][0], data.xpos[base][1]
    ctrl_period = 0.02  # 50 Hz 控制更新（与固件舵机刷新一致）
    next_ctrl, k = 0.0, 0
    min_h, max_tilt = 9.9, 0.0

    while data.time < t_end:
        if data.time >= next_ctrl:
            data.ctrl[:] = np.array(ctrl_fn(k * ctrl_period)) * D2R
            next_ctrl += ctrl_period
            k += 1
        mujoco.mj_step(model, data)
        h = data.xpos[base][2]
        r = data.xmat[base].reshape(3, 3)
        tilt = math.degrees(math.acos(min(1.0, abs(r[2, 2]))))
        min_h = min(min_h, h)
        max_tilt = max(max_tilt, tilt)

    dx = float(data.xpos[base][0] - x0)
    dy = float(data.xpos[base][1] - y0)
    return {
        "dx_m": round(dx, 3),
        "dy_m": round(dy, 3),
        "min_height_m": round(min_h, 4),
        "max_tilt_deg": round(max_tilt, 1),
        "fell": bool(min_h < 0.02 or max_tilt > 45),
    }


def passed_walk(m: dict, min_dx: float = 0.10) -> bool:
    return (not m["fell"]) and m["dx_m"] >= min_dx and abs(m["dy_m"]) < 0.25


# ---------------------------------------------------------------- 主流程

def search_walk(model: mujoco.MjModel) -> tuple[dict, dict]:
    """小网格搜索：freq × hip_mode × hip_sign × hip_amp × knee（对角 trot）。"""
    best, best_m = None, None
    print("\n== walk 参数搜索（2 s/组）==")
    print(f"{'freq':>5} {'mode':>7} {'sign':>4} {'amp':>4} {'knee':>4} | "
          f"{'dx':>6} {'dy':>6} {'tilt':>5} fell")
    for freq in (2.5, 3.3):
        for mode in ("same",):
            for sign in (1, -1):
                for amp in (28, 36, 44):
                    for knee in (50, 65):
                        for lead in (0, -45):
                            m = run(model, lambda t, f=freq, a=amp, mo=mode, sg=sign,
                                    k=knee, ld=lead:
                                    trot_ctrl(t, freq=f, hip_amp=a, hip_mode=mo,
                                              hip_sign=sg, knee_f=k, knee_r=k,
                                              knee_lead_deg=ld), t_end=2.0)
                            print(f"{freq:>5} {mode:>7} {sign:>4} {amp:>4} {knee:>4} "
                                  f"{lead:>4} | {m['dx_m']:>6} {m['dy_m']:>6} "
                                  f"{m['max_tilt_deg']:>5} {m['fell']}")
                            if passed_walk(m, min_dx=0.10) and (best_m is None or m["dx_m"] > best_m["dx_m"]):
                                best, best_m = (freq, mode, sign, amp, knee, lead), m
    if best:
        freq, mode, sign, amp, knee, lead = best
        best = {"freq": freq, "hip_amp": amp, "hip_mode": mode, "hip_sign": sign,
                "knee_f": knee, "knee_r": knee, "knee_lead_deg": lead}
    return best, best_m


def export_motion(params: dict, out: Path) -> None:
    """把选定的 trot 参数离散成 8 关键帧 .motion（规范 docs/21）。"""
    freq = params["freq"]
    period = 1.0 / freq
    n = 8
    frames = []
    for i in range(n):
        t = round(i * period / n * 1000)
        t = (t // 20) * 20  # 20 ms 网格
        ch = trot_ctrl(i * period / n, **params)
        frames.append({"t": t, "ch": [int(round(a)) for a in ch]})

    # 保证非降序且间隔 ≥ 20 ms
    for i in range(1, len(frames)):
        if frames[i]["t"] <= frames[i - 1]["t"]:
            frames[i]["t"] = frames[i - 1]["t"] + 20

    motion = {
        "name": "walk",
        "version": 1,
        "duration_ms": frames[-1]["t"],
        "loop": True,
        "tracks": {"servo": frames},
        "meta": {"author": "palmrobot", "source": "simulation",
                 "tags": ["gait", "trot"], "created": "2026-09-17"},
    }
    out.write_text(json.dumps(motion, ensure_ascii=False, separators=(",", ":")),
                   encoding="utf-8")
    print(f"\n已导出 {out}（{out.stat().st_size} B，{motion['duration_ms']} ms 循环）")


def main() -> int:
    quick = "--quick" in sys.argv
    model = load_model()
    print(f"MuJoCo {mujoco.__version__}，模型加载成功")

    print("\n== stand 站立 2 s ==")
    m_stand = run(model, lambda t: list(BASE), t_end=2.0)
    print(m_stand)

    print("\n== march 原地踏步 2 s ==")
    m_march = run(model, lambda t: march_ctrl(t, freq=1.67, knee_f=25, knee_r=25),
                  t_end=2.0)
    print(m_march)

    if quick:
        params = {"freq": 1.67, "hip_amp": 12, "hip_mode": "same", "hip_sign": 1,
                  "knee_f": 25, "knee_r": 25}
        m_walk = run(model, lambda t: trot_ctrl(t, **params), t_end=3.0)
        print("\n== walk 直线行走 3 s（默认参数）==")
        print(m_walk)
    else:
        best, _ = search_walk(model)
        if best is None:
            print("\n搜索无通过组合：先只保站立与踏步，行走待调（符合风险预案 S5/E5）")
            params = None
            m_walk = None
        else:
            print(f"\n选定参数：{best}")
            params = best
            m_walk = run(model, lambda t: trot_ctrl(t, **params), t_end=3.0)
            print("== walk 直线行走 3 s（选定参数）==")
            print(m_walk)

    result = {
        "date": "2026-09-17",
        "model": "sesame MJCF (lukehollis/sesame-ml, CAD-derived)",
        "stand": m_stand,
        "march": m_march,
        "gait": params,
        "walk": m_walk,
        "notes": [
            "ctrl 角度域 = 固件舵机角度域（0-180°），direction/subtrim 均为默认",
            "控制频率 50 Hz，与固件舵机刷新周期一致",
            "walk 通过标准：3 s 前进 ≥ 0.10 m（≈3.3 cm/s）、侧偏 < 0.25 m、不摔；Gate 1 的“1 米”在实物上复测",
        ],
    }
    (HERE / "gait_params.json").write_text(
        json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
    print("\n已写出 gait_params.json")

    if params and m_walk and not m_walk["fell"] and m_walk["dx_m"] >= 0.10:
        export_motion(params, HERE / "walk.motion")
    else:
        print("行走未达标，不导出 walk.motion")
    return 0


if __name__ == "__main__":
    sys.exit(main())
