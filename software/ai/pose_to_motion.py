#!/usr/bin/env python3
"""pose_to_motion.py — L2 精华级：视频人体姿态 → 8 自由度 .motion（docs/11 §6）

链路：视频 → MediaPipe PoseLandmarker(33 关键点/帧) → 低维特征
      → 重定向到 8 自由度（下表）→ EMA 平滑 → 关键帧下采样 → clamp 限位
      → .motion（非循环，长度≈视频长度）

重定向映射（"精华"级：8 自由度只能表达这些）：
  人体特征                    机器人动作
  ─────────────────────────   ─────────────────────────────────────
  髋中点 x 左右偏移            R1/L1 反向对摆（重心跟着画面左右移）
  髋中点 y 上下（相对中位）     四膝同步蹲起
  手腕高于同侧肩               该侧前腿抬起（wave 原型）

ref_x/ref_y 取全程中位数自校准（人不必站画面正中）。
关键帧间隔自适应收紧以满足 .motion 16KB 上限。

用法：
  python pose_to_motion.py <video> <out.motion>            # 视频输入
  python pose_to_motion.py <video> <out> --export lm.json  # 同时导出关键点
  python pose_to_motion.py <video> <out> --landmarks lm.json  # 复用/注入关键点
"""
from __future__ import annotations

import argparse
import json
import math
import re
import subprocess
import sys
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
DEFAULT_MODEL = HERE / "models" / "pose_landmarker_lite.task"
MOTION_GEN = HERE.parent / "tools" / "motion_gen.py"

# 规范常量（与 tools/motion_gen.py 一致，勿单方面修改）
STAND = [135, 45, 45, 135, 0, 180, 0, 180]
LIMITS = [(45, 180), (0, 135), (0, 135), (45, 180),
          (0, 180), (0, 180), (0, 180), (0, 180)]
MAX_FILE_BYTES = 16384
MIN_FRAME_GAP_MS = 20

# MediaPipe Pose 33 点索引（只用到这些）
R_SHO, L_SHO, R_WRIST, L_WRIST = 12, 11, 16, 15
R_HIP, L_HIP = 24, 23

# 重定向参数
HIP_X_GAIN = 0.25   # 髋 x 偏移满幅尺度（画面宽度比例）
HIP_AMP = 32        # 髋对摆最大半幅（°），与 L1 律动 medium/high 同级
SQUAT_GAIN = 0.2    # 髋 y 相对中位满幅尺度
SQUAT_AMP = 22      # 四膝蹲起最大（°）
ARM_H_GAIN = 0.15   # 手腕相对肩高度的满幅尺度（画面高度比例）
ARM_HIP_AMP = 32    # 举手→同侧髋收
ARM_KNEE_AMP = 35   # 举手→同侧前膝折叠
KNEE_SWING_AMP = 12 # 垂手→同侧腿外摆（踩点感）
SPREAD_GAIN = 0.30  # 双手横向开合满幅尺度（画面宽度比例）
SPREAD_AMP = 10     # 双手开合→重心横移
EMA = 0.55          # 平滑系数（连续特征取大，保留节奏顿挫）
TAIL_MS = 300       # 末帧回站立前的收尾间隔


# --------------------------- 前端：关键点获取 ---------------------------

def _ascii_copy(path: Path) -> Path:
    """mediapipe/cv2 的 C 层打不开中文路径（与 MuJoCo 同坑）：
    非 ASCII 路径先复制到临时目录再交给它们。"""
    try:
        str(path).encode("ascii")
        return path
    except UnicodeEncodeError:
        import shutil
        import tempfile
        tmp = Path(tempfile.gettempdir()) / ("palmrobot_" + path.name)
        shutil.copy2(path, tmp)
        return tmp


def detect_video(video: Path, model_path: Path, export: Path | None) -> list:
    """逐帧跑 PoseLandmarker，返回 [[(x, y, visibility) × 33], ...]。"""
    import cv2
    import mediapipe as mp
    from mediapipe.tasks.python import vision

    model_path = _ascii_copy(model_path)
    video_ascii = _ascii_copy(video)
    opts = vision.PoseLandmarkerOptions(
        base_options=mp.tasks.BaseOptions(model_asset_path=str(model_path)),
        running_mode=vision.RunningMode.VIDEO)
    cap = cv2.VideoCapture(str(video_ascii))
    if not cap.isOpened():
        raise RuntimeError(f"无法打开视频 {video}")
    fps = cap.get(cv2.CAP_PROP_FPS) or 30.0

    frames: list = []
    last = None
    with vision.PoseLandmarker.create_from_options(opts) as lm:
        i = 0
        while True:
            ok, bgr = cap.read()
            if not ok:
                break
            rgb = cv2.cvtColor(bgr, cv2.COLOR_BGR2RGB)
            img = mp.Image(image_format=mp.ImageFormat.SRGB, data=rgb)
            r = lm.detect_for_video(img, int(round(i * 1000.0 / fps)))
            if r.pose_landmarks:
                last = [(p.x, p.y, float(p.visibility))
                        for p in r.pose_landmarks[0]]
            # 未检出帧沿用上一帧关键点：保持帧数==视频帧数，时间轴不被压缩
            frames.append(last)
            i += 1
    cap.release()

    while frames and frames[0] is None:
        frames.pop(0)      # 开头未检出段丢弃（last 首次检出后永不为 None）
    if not frames:
        raise RuntimeError("全程未检测到人体——换一段有人体的视频")
    print(f"检测到人体帧: {len(frames)}（fps≈{fps:.1f}，模型 {model_path.name}）")

    if export is not None:
        payload = {"fps": fps, "frames": [[{"x": x, "y": y, "visibility": v}
                                           for x, y, v in f] for f in frames]}
        export.parent.mkdir(parents=True, exist_ok=True)
        export.write_text(json.dumps(payload), encoding="utf-8")
        print(f"关键点已导出 {export}")
    return frames, fps


def load_landmarks(path: Path) -> tuple[list, float]:
    """读关键点 JSON（--landmarks 注入/复用，与 --export 同格式）。"""
    data = json.loads(path.read_text(encoding="utf-8"))
    frames = [[(p["x"], p["y"], p.get("visibility", 1.0)) for p in f]
              for f in data["frames"]]
    if not frames:
        raise RuntimeError("关键点 JSON 为空")
    print(f"从 {path.name} 读入关键点帧: {len(frames)}")
    return frames, float(data.get("fps", 30.0))


# --------------------------- 重定向 ---------------------------

def extract_features(frames: list) -> list:
    """每帧 → (髋中 x, 髋中 y, 左手高, 右手高, 双手开合)。图像 y 向下。"""
    feats = []
    for f in frames:
        hx = (f[R_HIP][0] + f[L_HIP][0]) / 2.0
        hy = (f[R_HIP][1] + f[L_HIP][1]) / 2.0
        # 连续特征：手腕相对肩的高度（举手为正、垂下为负），与双手横向开合
        r_h = float(np.clip((f[R_SHO][1] - f[R_WRIST][1]) / ARM_H_GAIN, -1, 1))
        l_h = float(np.clip((f[L_SHO][1] - f[L_WRIST][1]) / ARM_H_GAIN, -1, 1))
        spread = float(np.clip((f[R_WRIST][0] - f[L_WRIST][0]) / SPREAD_GAIN, -1, 1))
        feats.append((hx, hy, l_h, r_h, spread))
    return feats


def redirect(frames: list, rate_ms: int, fps: float = 30.0) -> dict:
    """特征序列 → .motion servo 轨道。"""
    feats = extract_features(frames)
    ref_x = float(np.median([f[0] for f in feats]))
    ref_y = float(np.median([f[1] for f in feats]))

    # EMA 平滑（抖动抑制；连续特征 α 取大保留节奏顿挫）
    sm = list(feats[0])
    smoothed = []
    for feat in feats:
        sm = [v + EMA * (nv - v) for v, nv in zip(sm, feat)]
        smoothed.append(tuple(sm))

    # 关键帧：首帧站立，之后按 rate_ms 跳步采样（避免 30fps 全量帧超 16KB）
    # ceil 保证实际步距 ≥ rate_ms；t 用真实帧时间（i/fps），否则 step>1 时动作被加速
    step = max(1, math.ceil(fps * rate_ms / 1000.0))
    idxs = list(range(step, len(smoothed), step))   # i=0 已由首帧站立占用
    ms = lambda i: round(i / fps * 1000.0)
    servo = [{"t": 0, "ch": list(STAND)}]
    for i in idxs:
        hx, hy, l_h, r_h, spread = smoothed[i]
        dx = float(np.clip((hx - ref_x) / HIP_X_GAIN, -1, 1)) * HIP_AMP
        dy = float(np.clip((hy - ref_y) / SQUAT_GAIN, 0, 1)) * SQUAT_AMP
        ch = list(STAND)
        ch[0] = STAND[0] + dx + SPREAD_AMP * spread    # 双手开合 → 重心横移
        ch[2] = STAND[2] - dx + SPREAD_AMP * spread
        ch[4] = STAND[4] + dy; ch[7] = STAND[7] - dy   # 后膝折叠（蹲）
        ch[5] = STAND[5] - dy; ch[6] = STAND[6] + dy   # 前膝折叠（蹲）
        # 手臂连续高度 → 同侧腿连续屈伸：举手收腿，垂手外摆（踩点感）
        if r_h > 0:
            ch[0] -= ARM_HIP_AMP * r_h
            ch[5] -= ARM_KNEE_AMP * r_h
        else:
            ch[5] += KNEE_SWING_AMP * (-r_h)
        if l_h > 0:
            ch[2] += ARM_HIP_AMP * l_h
            ch[6] += ARM_KNEE_AMP * l_h
        else:
            ch[6] -= KNEE_SWING_AMP * (-l_h)
        ch = [int(round(min(max(ch[j], LIMITS[j][0]), LIMITS[j][1])))
              for j in range(8)]
        servo.append({"t": ms(i), "ch": ch})
    servo.append({"t": ms(len(smoothed) - 1) + TAIL_MS,
                  "ch": list(STAND)})   # 收尾回站立（真实视频末帧时间）
    return {"servo": servo, "duration_ms": servo[-1]["t"]}


def build_motion(video: Path, track: dict) -> dict:
    slug = re.sub(r"[^a-z0-9_-]", "-", video.stem.lower()).strip("-") or "pose"
    return {
        "name": f"pose-{slug[:27]}",
        "version": 1,
        "duration_ms": track["duration_ms"],
        "loop": False,
        "tracks": {"servo": track["servo"]},
        "meta": {"author": "palmrobot", "source": "ai-l2",
                 "tags": ["pose"], "created": "2026-09-18"},
    }


# --------------------------- CLI ---------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="L2 精华级：人体姿态→8 自由度 .motion")
    ap.add_argument("video", type=Path, help="视频（landmarks 注入时仍用于命名）")
    ap.add_argument("out", type=Path)
    ap.add_argument("--landmarks", type=Path, help="关键点 JSON（跳过 mediapipe）")
    ap.add_argument("--export", type=Path, help="把检测的关键点导出为 JSON")
    ap.add_argument("--model", type=Path, default=DEFAULT_MODEL)
    ap.add_argument("--no-validate", action="store_true")
    args = ap.parse_args()

    frames, fps = (load_landmarks(args.landmarks) if args.landmarks
                   else detect_video(args.video, args.model, args.export))

    # 间隔自适应：从 50ms 起步（动作细腻），超 16KB 再放宽（规范硬上限）
    track = None
    for rate in (50, 100, 150, 200, 300, 400, 500):
        track = redirect(frames, rate, fps)
        motion = build_motion(args.video, track)
        raw = json.dumps(motion, ensure_ascii=False, separators=(",", ":")).encode()
        if len(raw) <= MAX_FILE_BYTES:
            print(f"关键帧间隔 {rate} ms（{len(track['servo'])} 帧，{len(raw)} B）")
            break
    else:
        print("视频过长，压缩到 500ms 间隔仍超 16KB", file=sys.stderr)
        return 2

    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(raw)
    print(f"已生成 {args.out}（{len(raw)} B, {motion['duration_ms']} ms, 非 loop）")

    if not args.no_validate:
        r = subprocess.run([sys.executable, str(MOTION_GEN), "validate", str(args.out)],
                           capture_output=True, text=True)
        sys.stdout.write(r.stdout)
        if r.returncode != 0:
            print("校验失败")
            return 1
    return 0


if __name__ == "__main__":
    sys.exit(main())
