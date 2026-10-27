#!/usr/bin/env python3
"""video_to_motion.py — L1 律动级：视频 → .motion（docs/11 §6 三级路径的第一级）

链路：
  视频 → ffmpeg 抽音频(单声道 16k PCM) → RMS 能量包络(10ms 帧)
       → onset 自相关估 BPM（限 60–180）→ 每拍能量按分位数分档
       → 参数化律动摇摆原型（R1/L1 反向摆 + 前膝回弹）→ 循环 .motion

映射表：ai/rhythm_map.json（Python 与 JS 共读一份，docs/11 §6 约定）。
兜底原则：无音频流 / 静音 / 无清晰节拍 → BPM 100 + 中档幅度。任何输入必有输出。

用法：
  python video_to_motion.py <video> <out.motion> [--bpm N] [--beats N] [--no-validate]

产出后建议：
  python tools/motion_gen.py validate <out.motion>     # 格式校验
  python simulation/motion_player.py <out.motion>      # 仿真验证不摔
"""
from __future__ import annotations

import argparse
import json
import subprocess
import sys
import tempfile
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
MAP_PATH = HERE / "rhythm_map.json"
MOTION_GEN = HERE.parent / "tools" / "motion_gen.py"

SR = 16000          # 音频统一重采样到 16k 单声道
HOP_MS = 10         # 能量帧步长（ms）
HOP = SR * HOP_MS // 1000


# --------------------------- 映射表 ---------------------------

def load_map() -> dict:
    return json.loads(MAP_PATH.read_text(encoding="utf-8"))


# --------------------------- 音频分析 ---------------------------

def ffmpeg_exe() -> str:
    try:
        import imageio_ffmpeg
        return imageio_ffmpeg.get_ffmpeg_exe()
    except ImportError:
        return "ffmpeg"  # 系统安装的兜底


def extract_audio(video: Path) -> np.ndarray | None:
    """抽音频为 float 单声道 PCM（-1..1）。无音频流/解码失败返回 None。

    PCM 落到 ASCII 临时路径再读，避免任何工具链对中文路径的兼容问题。
    """
    cmd = [ffmpeg_exe(), "-y", "-nostdin", "-i", str(video), "-vn",
           "-ac", "1", "-ar", str(SR), "-f", "s16le"]
    try:
        with tempfile.TemporaryDirectory() as td:
            pcm_path = Path(td) / "a.pcm"
            r = subprocess.run(cmd + [str(pcm_path)], capture_output=True)
            if r.returncode != 0 or not pcm_path.exists():
                return None
            raw = pcm_path.read_bytes()
    except OSError:
        return None
    if len(raw) < SR * 2:  # 不足 1 s 音频视为无
        return None
    return np.frombuffer(raw, dtype=np.int16).astype(np.float32) / 32768.0


def energy_envelope(x: np.ndarray) -> np.ndarray:
    """每 HOP(10ms) 帧的 RMS。"""
    n = len(x) // HOP
    if n == 0:
        return np.zeros(1, dtype=np.float32)
    x = x[: n * HOP].reshape(n, HOP)
    return np.sqrt((x * x).mean(axis=1))


def estimate_bpm(rms: np.ndarray, peak_min: float) -> tuple[float, bool]:
    """onset 自相关估 BPM。返回 (bpm, 是否检测到清晰节拍)。

    onset novelty = RMS 一阶差的正半波 → 去均值 → 归一化自相关，
    在 60–180 BPM 对应滞后窗内取峰。峰弱/不足 2 s/全静音 → 兜底。
    """
    if len(rms) < 200:  # < 2 s 不够估拍
        return 100.0, False
    nov = np.maximum(np.diff(rms), 0.0)
    nov = nov - nov.mean()
    e0 = float(np.dot(nov, nov))
    if e0 <= 1e-12:
        return 100.0, False
    lag_min = max(2, round(60000 / 180 / HOP_MS))            # ≈33 帧
    lag_max = min(len(nov) - 1, round(60000 / 60 / HOP_MS))  # =100 帧
    if lag_max <= lag_min:
        return 100.0, False
    ac = np.correlate(nov, nov, mode="full")[len(nov) - 1:]
    seg = ac[lag_min: lag_max + 1] / e0
    best = int(np.argmax(seg)) + lag_min
    if float(seg[best - lag_min]) < peak_min:
        return 100.0, False
    bpm = 60000.0 / (best * HOP_MS)
    return float(min(180.0, max(60.0, bpm))), True


# --------------------------- 动作生成 ---------------------------

def band_levels(rms: np.ndarray, beats: int, beat_ms: float,
                quantiles: list[float]) -> list[str]:
    """把时间轴切成 beats 拍，按每拍平均能量相对全局分位数分 low/medium/high。"""
    lo, hi = np.quantile(rms, quantiles)
    per_beat = max(1, round(beat_ms / HOP_MS))
    levels = []
    for b in range(beats):
        seg = rms[b * per_beat: (b + 1) * per_beat]
        v = float(seg.mean()) if len(seg) else 0.0
        levels.append("low" if v <= lo else ("high" if v >= hi else "medium"))
    return levels


def sway_frame(stand: list[int], ch_map: dict, left: bool,
               hip_amp: int, knee_amp: int) -> list[int]:
    """摇摆半拍关键帧：R1/L1 反向摆（重心左右交替）+ 前膝同步回弹。"""
    ch = list(stand)
    hr, hl = ch_map["hip_r"], ch_map["hip_l"]
    if left:
        ch[hr] = stand[hr] - hip_amp
        ch[hl] = stand[hl] + hip_amp
    else:
        ch[hr] = stand[hr] + hip_amp
        ch[hl] = stand[hl] - hip_amp
    if knee_amp:
        ch[ch_map["knee_fr"]] = stand[ch_map["knee_fr"]] - knee_amp
        ch[ch_map["knee_fl"]] = stand[ch_map["knee_fl"]] + knee_amp
    return ch


def build_motion(m: dict, bpm: float, levels: list[str]) -> dict:
    """8 拍循环：t=0 站立，之后每半拍 交替[摇摆帧/回站立]。

    幅度取该拍档位；末帧=站立，与 loop 首帧一致，循环衔接平滑。
    """
    stand: list[int] = m["pose_stand"]
    bands_cfg = m["bands"]
    ch_map = m["channels"]
    gap = m["min_frame_gap_ms"]
    n_beats = len(levels)

    half = 60000.0 / bpm / 2.0
    if round(half) < gap:
        raise ValueError(f"半拍 {half:.0f}ms < 最小帧距 {gap}ms（BPM 过高）")

    frames = [{"t": 0, "ch": list(stand)}]
    prev_t = 0
    for i in range(1, 2 * n_beats + 1):
        t = round(i * half)
        if t - prev_t < gap:
            t = prev_t + gap
        prev_t = t
        if i % 2 == 1:  # 摇摆帧：奇数半拍，左右交替
            cfg = bands_cfg[levels[(i - 1) // 2]]
            left = ((i - 1) // 2) % 2 == 0
            ch = sway_frame(stand, ch_map, left, cfg["hip_amp"], cfg["knee_amp"])
        else:           # 回站立
            ch = list(stand)
        frames.append({"t": t, "ch": ch})

    return {
        "name": f"rhythm-{round(bpm)}bpm",
        "version": 1,
        "duration_ms": prev_t,
        "loop": True,
        "tracks": {"servo": frames},
        "meta": {"author": "palmrobot", "source": "ai-l1",
                 "tags": ["rhythm"], "created": "2026-09-17"},
    }


# --------------------------- CLI ---------------------------

def main() -> int:
    ap = argparse.ArgumentParser(description="L1 律动级：视频→.motion")
    ap.add_argument("video", type=Path)
    ap.add_argument("out", type=Path)
    ap.add_argument("--bpm", type=float, help="跳过检测，直接指定 BPM（60–180）")
    ap.add_argument("--beats", type=int, default=None,
                    help="循环拍数（默认映射表 beats_per_loop=8）")
    ap.add_argument("--no-validate", action="store_true",
                    help="跳过 motion_gen validate 子校验")
    args = ap.parse_args()

    m = load_map()

    # 1) 音频与节拍（音频只抽一次，rms 后面复用）
    rms: np.ndarray | None = None
    if args.bpm is not None:
        bpm, source = float(min(180.0, max(60.0, args.bpm))), "cli-override"
    else:
        pcm = extract_audio(args.video)
        if pcm is None:
            print("[兜底] 未检出音频流 → BPM 100 中档")
            bpm, source = float(m["bpm"]["default_silent"]), "fallback:no-audio"
        else:
            rms = energy_envelope(pcm)
            if float(rms.max()) < 1e-3:
                print("[兜底] 音频静音 → BPM 100 中档")
                bpm, source = float(m["bpm"]["default_silent"]), "fallback:silent"
                rms = None
            else:
                bpm, found = estimate_bpm(rms, m["onset_peak_min"])
                source = "detected" if found else "fallback:weak-beat"
                if not found:
                    print("[兜底] 无清晰节拍 → BPM 100 中档")

    beat_ms = 60000.0 / bpm
    n_beats = args.beats or m["beats_per_loop"]

    # 2) 能量分档（无 rms（兜底/覆盖）→ 全中档）
    if rms is None:
        levels = ["medium"] * n_beats
    else:
        levels = band_levels(rms, n_beats, beat_ms, m["energy_quantiles"])

    # 3) 生成
    motion = build_motion(m, bpm, levels)
    raw = json.dumps(motion, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    args.out.parent.mkdir(parents=True, exist_ok=True)
    args.out.write_bytes(raw)

    dist = {lv: levels.count(lv) for lv in ("low", "medium", "high") if levels.count(lv)}
    print(f"BPM: {bpm:.1f}（{source}）  档位分布: {dist}")
    print(f"已生成 {args.out}（{len(raw)} B, {motion['duration_ms']} ms, "
          f"{n_beats} 拍 {len(frames := motion['tracks']['servo'])} 帧, loop）")

    # 4) 校验
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
