#!/usr/bin/env python3
"""motion_player.py — 把任意 .motion 动作包放进 MuJoCo 物理仿真播放，验证摔不摔。

"视频转动作"链路的最后一环：生成的 .motion 必须先在仿真里证明不摔，
才能下发真机。插值语义与固件一致（docs/21 §2.1：关键帧线性插值，loop 末帧→首帧）。

用法：
  .venv/Scripts/python motion_player.py ../tools/motions/walk.motion
  .venv/Scripts/python motion_player.py ../ai/out/wave1.motion --png out.png
  .venv/Scripts/python motion_player.py <file> --json report.json
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

import mujoco
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from sim_load import load_model, JOINT_ORDER  # noqa: E402

# 摔倒判定（播放动作比站立放宽：倾角 45°）
FALL_HEIGHT = 0.02       # 身体中心低于此值（m）
FALL_TILT_DEG = 45.0     # 倾角超过此值（°）
FALL_CONFIRM_S = 0.3     # 连续超限此时长才判摔（单帧毛刺不算）


def _write_png(path: Path, rgb: "np.ndarray") -> None:
    """H×W×3 uint8 → PNG（纯 stdlib 编码，不引 PIL）。"""
    import struct
    import zlib

    h, w, _ = rgb.shape
    row = rgb.tobytes()

    def chunk(tag: bytes, payload: bytes) -> bytes:
        return (struct.pack(">I", len(payload)) + tag + payload
                + struct.pack(">I", zlib.crc32(tag + payload) & 0xFFFFFFFF))

    raw = b"".join(b"\x00" + row[y * w * 3:(y + 1) * w * 3] for y in range(h))
    path.write_bytes(
        b"\x89PNG\r\n\x1a\n"
        + chunk(b"IHDR", struct.pack(">IIBBBBB", w, h, 8, 2, 0, 0, 0))
        + chunk(b"IDAT", zlib.compress(raw, 6))
        + chunk(b"IEND", b""))


def load_motion(path: Path) -> dict:
    m = json.loads(path.read_text(encoding="utf-8"))
    if m.get("version") != 1:
        raise ValueError("仅支持 version=1")
    frames = m["tracks"]["servo"]
    if not frames or frames[0]["t"] != 0:
        raise ValueError("servo 轨道为空或首帧 t != 0")
    return m


def sample_motion(m: dict, t_ms: float) -> list[float]:
    """固件同款插值：非降序关键帧线性插值；loop=true 时末帧→首帧循环。"""
    frames = [dict(f) for f in m["tracks"]["servo"]]
    duration = m["duration_ms"]
    if m.get("loop") and frames[-1]["t"] < duration:
        frames.append({"t": duration, "ch": list(frames[0]["ch"])})
    if t_ms >= frames[-1]["t"]:
        return list(frames[-1]["ch"])
    lo, hi = 0, len(frames) - 1
    while lo < hi - 1:
        mid = (lo + hi) // 2
        if frames[mid]["t"] <= t_ms:
            lo = mid
        else:
            hi = mid
    f0, f1 = frames[lo], frames[hi]
    k = (t_ms - f0["t"]) / max(1, f1["t"] - f0["t"])
    return [a + (b - a) * k for a, b in zip(f0["ch"], f1["ch"])]


def play(motion: dict, png: Path | None = None) -> dict:
    model = load_model()
    data = mujoco.MjData(model)

    act_ids = [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, f"{n}_servo")
               for n in JOINT_ORDER]
    base_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "base")

    duration_s = motion["duration_ms"] / 1000.0
    dt = model.opt.timestep
    fall_at: float | None = None
    over_since = 0.0
    min_h, max_tilt = 9.9, 0.0

    renderer = None
    if png is not None:
        renderer = mujoco.Renderer(model, height=480, width=640)

    while data.time < duration_s:
        angles = sample_motion(motion, data.time * 1000.0)
        for act, a in zip(act_ids, angles):
            data.ctrl[act] = math.radians(a)   # ctrl 域 == 舵机角度域
        mujoco.mj_step(model, data)

        h = float(data.xpos[base_id][2])
        tilt = math.degrees(math.acos(float(np.clip(data.xmat[base_id][8], -1, 1))))
        min_h = min(min_h, h)
        max_tilt = max(max_tilt, tilt)

        fallen = h < FALL_HEIGHT or tilt > FALL_TILT_DEG
        over_since = over_since + dt if fallen else 0.0
        if over_since >= FALL_CONFIRM_S and fall_at is None:
            fall_at = data.time
            break

    if renderer is not None and png is not None:
        angles = sample_motion(motion, (fall_at or duration_s) * 1000.0)
        for act, a in zip(act_ids, angles):
            data.ctrl[act] = math.radians(a)
        mujoco.mj_forward(model, data)
        renderer.update_scene(data, camera="front_camera")
        _write_png(png, renderer.render())
        renderer.close()

    return {
        "motion": motion.get("name"),
        "duration_s": duration_s,
        "played_s": round(fall_at or duration_s, 2),
        "fall": fall_at is not None,
        "fall_at_s": round(fall_at, 2) if fall_at is not None else None,
        "min_height_m": round(min_h, 4),
        "max_tilt_deg": round(max_tilt, 1),
        "verdict": "FALL" if fall_at is not None else "PASS",
    }


def _report_viewer_window() -> None:
    """诊断：确认 MuJoCo 窗口是否真的创建在本桌面（沙箱可能隔离 window station）。"""
    import ctypes

    user32 = ctypes.windll.user32
    hwnd = user32.FindWindowW(None, "MuJoCo")
    if hwnd:
        print(f"[view] MuJoCo 窗口已创建 hwnd={hwnd:#x} "
              f"visible={bool(user32.IsWindowVisible(hwnd))}", flush=True)
    else:
        print("[view] 警告：未找到 MuJoCo 窗口（可能创建在了不可见的隔离桌面）", flush=True)


def play_interactive(motion: dict) -> None:
    import time as _time

    import mujoco.viewer
    model = load_model()
    data = mujoco.MjData(model)
    act_ids = [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, f"{n}_servo")
               for n in JOINT_ORDER]
    duration_s = motion["duration_ms"] / 1000.0
    loop = bool(motion.get("loop"))
    with mujoco.viewer.launch_passive(model, data) as v:
        _report_viewer_window()
        t0 = data.time
        wall0 = _time.perf_counter()
        next_report = 5.0
        while v.is_running():   # 窗口由用户关闭退出
            step_start = _time.perf_counter()
            t_rel = (data.time - t0) % duration_s if loop else \
                min(data.time - t0, duration_s)  # 非 loop 播完停在末帧
            angles = sample_motion(motion, t_rel * 1000.0)
            for act, a in zip(act_ids, angles):
                data.ctrl[act] = math.radians(a)
            mujoco.mj_step(model, data)
            v.sync()
            # 实时同步：睡满一个 timestep，防止全速快进（官方示例同款写法）
            delay = step_start + model.opt.timestep - _time.perf_counter()
            if delay > 0:
                _time.sleep(delay)
            wall = _time.perf_counter() - wall0
            if wall >= next_report:
                print(f"[view] 已播 {t_rel:6.1f}s / {duration_s:.1f}s  "
                      f"真实 {wall:6.1f}s  running={v.is_running()}", flush=True)
                next_report += 5.0
    print(f"[view] 循环退出（is_running=False，窗口被关闭）"
          f"  真实耗时 {_time.perf_counter() - wall0:.1f}s", flush=True)


def record(motion: dict, out_mp4: Path) -> dict:
    """离屏渲染整段动作并拼成 MP4（GLFW 交互窗口不可用环境的替代预览）。"""
    import tempfile

    from PIL import Image

    model = load_model()
    data = mujoco.MjData(model)
    act_ids = [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, f"{n}_servo")
               for n in JOINT_ORDER]
    base_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "base")
    duration_s = motion["duration_ms"] / 1000.0
    dt = model.opt.timestep
    fall_at: float | None = None
    over_since = 0.0
    min_h, max_tilt = 9.9, 0.0

    snap_dir = Path(tempfile.mkdtemp(prefix="palmrobot_rec_"))
    snap_s = 0.1                      # 10 fps 预览
    frame_i = 0
    with mujoco.Renderer(model, height=480, width=640) as r:
        cam = mujoco.MjvCamera()
        cam.lookat[:] = [0, 0, 0.05]
        cam.distance = 0.35
        cam.azimuth = 135
        cam.elevation = -25
        while data.time < duration_s:
            angles = sample_motion(motion, data.time * 1000.0)
            for act, a in zip(act_ids, angles):
                data.ctrl[act] = math.radians(a)
            mujoco.mj_step(model, data)
            h = float(data.xpos[base_id][2])
            tilt = math.degrees(math.acos(float(np.clip(data.xmat[base_id][8], -1, 1))))
            min_h = min(min_h, h)
            max_tilt = max(max_tilt, tilt)
            fallen = h < FALL_HEIGHT or tilt > FALL_TILT_DEG
            over_since = over_since + dt if fallen else 0.0
            if over_since >= FALL_CONFIRM_S and fall_at is None:
                fall_at = data.time
            if fall_at is None and data.time >= frame_i * snap_s:
                mujoco.mj_forward(model, data)
                r.update_scene(data, camera=cam)
                Image.fromarray(r.render()).save(snap_dir / f"{frame_i:05d}.png")
                frame_i += 1
            if fall_at is not None:
                break

    # ffmpeg 拼 MP4（沿用 imageio-ffmpeg 静态二进制）
    try:
        import imageio_ffmpeg
        ffmpeg = imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        ffmpeg = "ffmpeg"
    subprocess.run([ffmpeg, "-y", "-loglevel", "error",
                    "-framerate", str(round(1 / snap_s)),
                    "-i", str(snap_dir / "%05d.png"),
                    "-c:v", "libx264", "-pix_fmt", "yuv420p", str(out_mp4)],
                   check=True)
    for p in snap_dir.glob("*.png"):
        p.unlink()
    snap_dir.rmdir()

    report = {
        "motion": motion.get("name"),
        "duration_s": duration_s,
        "played_s": round(fall_at or duration_s, 2),
        "fall": fall_at is not None,
        "fall_at_s": round(fall_at, 2) if fall_at is not None else None,
        "min_height_m": round(min_h, 4),
        "max_tilt_deg": round(max_tilt, 1),
        "frames": frame_i,
        "video": str(out_mp4),
        "verdict": "FALL" if fall_at is not None else "PASS",
    }
    print(json.dumps(report, ensure_ascii=False))
    return report


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("motion", type=Path)
    ap.add_argument("--png", type=Path, help="渲染末帧 PNG")
    ap.add_argument("--view", action="store_true", help="交互 viewer 播放")
    ap.add_argument("--record", type=Path, help="离屏渲染整段动作并输出 MP4")
    ap.add_argument("--json", type=Path, help="报告写 JSON")
    args = ap.parse_args()

    motion = load_motion(args.motion)
    if args.view:
        play_interactive(motion)
        return 0
    if args.record:
        report = record(motion, args.record)
        if args.json:
            args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2),
                                 encoding="utf-8")
        return 1 if report["fall"] else 0

    report = play(motion, png=args.png)
    print(json.dumps(report, ensure_ascii=False))
    if args.json:
        args.json.write_text(json.dumps(report, ensure_ascii=False, indent=2), encoding="utf-8")
    return 1 if report["fall"] else 0


if __name__ == "__main__":
    sys.exit(main())
