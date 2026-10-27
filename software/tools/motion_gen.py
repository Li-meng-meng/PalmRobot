#!/usr/bin/env python3
"""motion_gen.py — .motion 动作包校验与生成工具（P0）

规范：docs/21-动作文件格式规范.md（v1.0 冻结）
指令集：docs/20-指令集规范.md（v1.0 冻结）

用法：
  python motion_gen.py validate <file|dir>   校验单个或目录下全部 .motion
  python motion_gen.py gen <preset> <out>    生成动作文件（stand/wave/dance/walk）
  python motion_gen.py beautify <in> [out]   缓动烘焙：关键帧样条化重采样（更顺滑）
  python motion_gen.py stats <dir>           汇总动作包数量与大小

仅用标准库，无第三方依赖。
"""
from __future__ import annotations

import argparse
import bisect
import json
import sys
from pathlib import Path

# ---- 规范常量（与 docs/21 冻结值一致，勿单方面修改）----
FORMAT_VERSION = 1
MAX_FILE_BYTES = 16384          # 硬上限，与指令集 PUTM 一致
SERVO_CH_COUNT = 8              # 8 自由度
FACE_PARAM_COUNT = 12           # 表情十二维
MIN_FRAME_GAP_MS = 20           # 相邻 servo 帧最小间隔（舵机刷新周期）
DURATION_RANGE = (100, 600_000)
ANGLE_RANGE = (0, 180)
GAZE_RANGE = (-100, 100)
EXPR_RANGE = (0, 100)
MID_ANGLE = 90                  # 中位角

# 指令集 §6 表情参数顺序：eyeOpen squint browHeight browAngle mouthOpen
#                       mouthCurve mouthWide pupilSize gazeX gazeY cheek bright
FACE_HAPPY = [80, 10, 60, 40, 30, 90, 60, 50, 0, 0, 40, 80]
FACE_NEUTRAL = [50, 0, 50, 50, 10, 50, 50, 50, 0, 0, 20, 80]

# 通道序（= assets/calibration/default.yaml joint_order，与 MJCF 执行器顺序一致）：
# ch0=R1 前右髋  ch1=R2 后右髋  ch2=L1 前左髋  ch3=L2 后左髋
# ch4=R4 后右膝  ch5=R3 前右膝  ch6=L3 前左膝  ch7=L4 后左膝
# 预设姿态关键帧（规范 §5，基准 = 仿真标定 stand_degrees）
POSE_STAND = [135, 45, 45, 135, 0, 180, 0, 180]
POSE_SIT = [135, 75, 45, 75, 50, 170, 20, 170]
POSE_SLEEP = [135, 80, 45, 80, 60, 160, 20, 160]

# 关节限位（default.yaml limits_degrees）
LIMITS = [(45, 180), (0, 135), (0, 135), (45, 180),
          (0, 180), (0, 180), (0, 180), (0, 180)]


# ============================ 校验 ============================

def validate_motion(data: dict, raw_size: int) -> list[str]:
    """按规范 §4 的 9 条规则校验，返回错误列表（空列表 = 通过）。"""
    errors: list[str] = []

    def err(pos: str, msg: str) -> None:
        errors.append(f"{pos}: {msg}")

    if not isinstance(data, dict):
        return ["<root>: 顶层必须是 JSON 对象"]

    # 1. version
    if data.get("version") != FORMAT_VERSION:
        err("version", f"必须为 {FORMAT_VERSION}，实际 {data.get('version')!r}")

    # 2. name
    name = data.get("name")
    if not isinstance(name, str) or not (1 <= len(name) <= 32):
        err("name", "必须为 1–32 字符字符串")
    elif any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_-" for c in name):
        err("name", "仅允许 [a-z0-9_-]")

    # duration_ms
    dur = data.get("duration_ms")
    if not isinstance(dur, int) or isinstance(dur, bool) or not (DURATION_RANGE[0] <= dur <= DURATION_RANGE[1]):
        err("duration_ms", f"必须为 {DURATION_RANGE[0]}–{DURATION_RANGE[1]} 的整数")

    if not isinstance(data.get("loop"), bool):
        err("loop", "必须为布尔值")

    tracks = data.get("tracks")
    if not isinstance(tracks, dict):
        err("tracks", "必须为对象且至少含 servo")
        return errors

    # ---- servo 轨道（必需）----
    servo = tracks.get("servo")
    if not isinstance(servo, list) or not servo:
        err("tracks.servo", "必须为非空数组")
        return errors

    prev_t: int | None = None
    first_t: int | None = None
    for i, frame in enumerate(servo):
        pos = f"tracks.servo[{i}]"
        if not isinstance(frame, dict):
            err(pos, "必须是对象")
            continue
        t = frame.get("t")
        if not isinstance(t, int) or isinstance(t, bool) or t < 0:
            err(pos + ".t", "必须为非负整数")
            continue
        if prev_t is not None:
            if t < prev_t:
                err(pos + ".t", f"时间必须非降序（{t} < 前帧 {prev_t}）")
            elif t - prev_t < MIN_FRAME_GAP_MS:
                err(pos + ".t", f"相邻帧间隔 {t - prev_t}ms < 最小 {MIN_FRAME_GAP_MS}ms")
        prev_t = t
        if first_t is None:
            first_t = t

        ch = frame.get("ch")
        if not isinstance(ch, list) or len(ch) != SERVO_CH_COUNT:
            err(pos + ".ch", f"必须为 {SERVO_CH_COUNT} 个角度")
            continue
        for j, a in enumerate(ch):
            if not isinstance(a, int) or isinstance(a, bool) or not (ANGLE_RANGE[0] <= a <= ANGLE_RANGE[1]):
                err(f"{pos}.ch[{j}]", f"角度必须为 {ANGLE_RANGE[0]}–{ANGLE_RANGE[1]} 的整数，实际 {a!r}")

    if first_t != 0:
        err("tracks.servo", "首帧 t 必须为 0（定义初始姿态）")

    # ---- face 轨道（可选）----
    face = tracks.get("face")
    if face is not None:
        if not isinstance(face, list):
            err("tracks.face", "必须为数组")
        else:
            prev_ft: int | None = None
            for i, frame in enumerate(face):
                pos = f"tracks.face[{i}]"
                if not isinstance(frame, dict):
                    err(pos, "必须是对象")
                    continue
                t = frame.get("t")
                if not isinstance(t, int) or isinstance(t, bool) or t < 0:
                    err(pos + ".t", "必须为非负整数")
                    continue
                if prev_ft is not None and t < prev_ft:
                    err(pos + ".t", f"时间必须非降序（{t} < 前帧 {prev_ft}）")
                prev_ft = t

                expr = frame.get("expr")
                if not isinstance(expr, list) or len(expr) != FACE_PARAM_COUNT:
                    err(pos + ".expr", f"必须为 {FACE_PARAM_COUNT} 个参数")
                    continue
                for j, v in enumerate(expr):
                    if not isinstance(v, int) or isinstance(v, bool):
                        err(f"{pos}.expr[{j}]", f"必须为整数，实际 {v!r}")
                        continue
                    lo, hi = GAZE_RANGE if j in (8, 9) else EXPR_RANGE
                    if not (lo <= v <= hi):
                        err(f"{pos}.expr[{j}]", f"超出范围 {lo}–{hi}，实际 {v}")

    # ---- audio 轨道（可选）----
    audio = tracks.get("audio")
    if audio is not None:
        if not isinstance(audio, list):
            err("tracks.audio", "必须为数组")
        else:
            for i, frame in enumerate(audio):
                pos = f"tracks.audio[{i}]"
                if not isinstance(frame, dict):
                    err(pos, "必须是对象")
                    continue
                clip = frame.get("clip")
                if not isinstance(clip, str) or not (1 <= len(clip) <= 32) \
                        or any(c not in "abcdefghijklmnopqrstuvwxyz0123456789_." for c in clip):
                    err(pos + ".clip", "必须为 1–32 字符且仅含 [a-z0-9_.]")

    # ---- 时间轴上限 ----
    if isinstance(dur, int):
        for i, frame in enumerate(servo):
            if isinstance(frame, dict) and isinstance(frame.get("t"), int) and frame["t"] > dur:
                err(f"tracks.servo[{i}].t", f"{frame['t']} 超过 duration_ms={dur}")

    # ---- 文件大小 ----
    if raw_size > MAX_FILE_BYTES:
        err("<file>", f"大小 {raw_size} 字节超过硬上限 {MAX_FILE_BYTES}")

    return errors


# ============================ 生成器 ============================

def _mk(name: str, dur: int, loop: bool, servo: list[dict],
        face: list[dict] | None = None, source: str = "manual") -> dict:
    tracks: dict = {"servo": servo}
    if face:
        tracks["face"] = face
    return {
        "name": name,
        "version": FORMAT_VERSION,
        "duration_ms": dur,
        "loop": loop,
        "tracks": tracks,
        "meta": {"author": "palmrobot", "source": source,
                 "tags": [], "created": "2026-09-17"},
    }


def _frame(t: int, ch: list[int]) -> dict:
    return {"t": t, "ch": list(ch)}


def _face_frame(t: int, expr: list[int]) -> dict:
    return {"t": t, "expr": list(expr)}


def gen_stand() -> dict:
    return _mk("stand", 200, False, [_frame(0, POSE_STAND)])


def gen_wave() -> dict:
    """挥手：抬起右前腿（R1 髋 + R3 膝折叠）摆动两次，其余腿站立。"""
    base = POSE_STAND
    up = list(base); up[0] = 115; up[5] = 150       # R1 收髋、R3 折膝抬起右前腿
    swing_a = list(up); swing_a[0] = 100            # 前摆
    swing_b = list(up); swing_b[0] = 130            # 后摆
    servo = [
        _frame(0, base),
        _frame(300, up),
        _frame(550, swing_a),
        _frame(800, swing_b),
        _frame(1050, swing_a),
        _frame(1300, up),
        _frame(1700, base),
    ]
    face = [_face_frame(0, FACE_HAPPY)]
    return _mk("wave", 1700, False, servo, face)


def gen_dance() -> dict:
    """摇摆：R1/L1 反向摆动使重心左右交替，循环播放。"""
    left = list(POSE_STAND); left[0] = 120; left[2] = 60
    right = list(POSE_STAND); right[0] = 150; right[2] = 30
    servo = [
        _frame(0, POSE_STAND),
        _frame(200, left),
        _frame(450, POSE_STAND),
        _frame(650, right),
        _frame(900, POSE_STAND),
    ]
    face = [_face_frame(0, FACE_HAPPY)]
    return _mk("dance", 900, True, servo, face)


def gen_walk() -> dict:
    """直线行走参考步态：对角小跑（trot）四相位循环。

    对角组 A = R1 前右髋 + L2 后左髋（膝：R3/L4），B = L1 前左髋 + R2 后右髋（膝：L3/R4）。
    注意：这是**未经仿真验证的参考序列**，正式步态参数以 simulation/ 仿真调参产物为准。
    """
    hip_amp = 12            # 髋摆幅 ±12°
    knee_f_lift = 25        # 前膝折叠（180 → 155）
    knee_r_lift = 25        # 后膝抬起（0 → 25）

    def trot_frame(swing: str | None) -> list[int]:
        ch = list(POSE_STAND)
        if swing == "A":                    # A 组摆动相
            ch[0] += hip_amp; ch[3] -= hip_amp
            ch[5] = 180 - knee_f_lift       # R3 前右膝折叠
            ch[7] = knee_r_lift             # L4 后左膝抬起
        elif swing == "B":                  # B 组摆动相
            ch[2] -= hip_amp; ch[1] += hip_amp
            ch[6] = 180 - knee_f_lift       # L3 前左膝折叠
            ch[4] = knee_r_lift             # R4 后右膝抬起
        return ch

    frames = [ _frame(0, POSE_STAND),
               _frame(150, trot_frame("A")),
               _frame(300, POSE_STAND),
               _frame(450, trot_frame("B")) ]
    return _mk("walk", 600, True, frames, source="simulation")


PRESETS = {
    "stand": gen_stand,
    "wave": gen_wave,
    "dance": gen_dance,
    "walk": gen_walk,
}


# ============================ 缓动烘焙（beautify）============================
#
# 规范（docs/21 §2.1）固件只做关键帧线性插值，但帧间隔下限 20ms（=50 Hz），
# 足以把任何缓动曲线"烤"进关键帧序列——平滑发生在生成端，固件零改动。
# 做法：过原关键帧建 Catmull-Rom 样条（loop 时首尾切线互绕），
# 在 |曲线-弦| 偏差 ≤ tol 的约束下贪心拉大帧距（20ms 步进、上限 max_gap），
# 超字节预算自动放宽 tol 重采，输出仍是普通 .motion，三端无感知。


def _cr_slopes(ts: list[int], vals: list[float], wrap_t: int | None) -> list[float]:
    """Catmull-Rom 斜率（d val/d t）。wrap_t 给定时首尾互绕（循环动作闭合）。"""
    n = len(ts)
    m = [0.0] * n
    for k in range(n):
        if k == 0:
            if wrap_t is not None:
                tp, vp = ts[-1] - wrap_t, vals[-1]
            else:
                tp, vp = ts[0], vals[0]           # 端点零斜率 → 起步缓入
        else:
            tp, vp = ts[k - 1], vals[k - 1]
        if k == n - 1:
            if wrap_t is not None:
                tn, vn = ts[0] + wrap_t, vals[0]
            else:
                tn, vn = ts[n - 1], vals[n - 1]   # 端点零斜率 → 收势缓出
        else:
            tn, vn = ts[k + 1], vals[k + 1]
        dt = tn - tp
        m[k] = (vn - vp) / dt if dt > 0 else 0.0
    return m


def _cr_eval(ts: list[int], vals: list[float], slopes: list[float], t: float) -> float:
    """三次 Hermite 求值（t 钳在首末结点区间内）。"""
    i = min(max(bisect.bisect_right(ts, t) - 1, 0), len(ts) - 2)
    h = ts[i + 1] - ts[i]
    u = (t - ts[i]) / h if h > 0 else 0.0
    u2, u3 = u * u, u * u * u
    return ((2 * u3 - 3 * u2 + 1) * vals[i] + (u3 - 2 * u2 + u) * h * slopes[i]
            + (-2 * u3 + 3 * u2) * vals[i + 1] + (u3 - u2) * h * slopes[i + 1])


def beautify_motion(data: dict, tol: float = 1.0, max_gap: int = 200,
                    budget: int = 10240) -> tuple[dict, str]:
    """缓动烘焙。返回（新 motion dict, 说明文本）；不修改入参。"""
    servo = data["tracks"]["servo"]
    dur, loop = data["duration_ms"], data["loop"]
    if len(servo) < 2:
        return json.loads(json.dumps(data)), "单帧无轨道可美化，原样保留"

    ts = [f["t"] for f in servo]
    vals = [list(f["ch"]) for f in servo]
    n_ch = len(vals[0])

    # loop 且末帧早于 duration：把"末帧→首帧"循环段并入曲线（与固件 loop 语义一致）
    eff_ts, eff_vals = list(ts), [list(v) for v in vals]
    wrap_t = None
    if loop:
        if ts[-1] < dur:
            eff_ts.append(dur)
            eff_vals.append(list(vals[0]))
        wrap_t = dur                              # 首尾切线互绕，循环点速度连续
    curves = []
    for c in range(n_ch):
        v = [row[c] for row in eff_vals]
        curves.append((v, _cr_slopes(eff_ts, v, wrap_t)))
    emit_end = dur if loop else eff_ts[-1]        # 非 loop：末帧之后是保持，无需帧

    def sample(t: float) -> list[int]:
        return [int(round(min(max(_cr_eval(eff_ts, v, s, t), 0.0), 180.0)))
                for v, s in curves]

    def chord_dev(t0: int, t1: int, a0: list[int], a1: list[int]) -> float:
        """曲线偏离弦的最大角度差（全通道）。"""
        worst = 0.0
        n_inner = max(2, min(6, (t1 - t0) // MIN_FRAME_GAP_MS))
        for j in range(1, n_inner + 1):
            tm = t0 + (t1 - t0) * j / (n_inner + 1)
            k = (tm - t0) / (t1 - t0)
            for c, (v, s) in enumerate(curves):
                dev = abs(_cr_eval(eff_ts, v, s, tm) - (a0[c] + (a1[c] - a0[c]) * k))
                worst = max(worst, dev)
        return worst

    cur_tol = tol
    while True:
        out_t = [0]
        out_ch = [sample(0)]
        t = 0
        while t < emit_end:
            if emit_end - t < MIN_FRAME_GAP_MS:
                break                             # 剩余不足一帧：留给收尾 emit_end 帧
            hi = min(t + max_gap, emit_end)
            cand_list = list(range(t + MIN_FRAME_GAP_MS, hi + 1, MIN_FRAME_GAP_MS))
            if emit_end > t + MIN_FRAME_GAP_MS and emit_end <= hi \
                    and (not cand_list or cand_list[-1] != emit_end):
                cand_list.append(emit_end)
            chosen = t + MIN_FRAME_GAP_MS
            for c in reversed(cand_list):
                if chord_dev(t, c, out_ch[-1], sample(c)) <= cur_tol:
                    chosen = c
                    break
            if out_t[-1] != emit_end and emit_end - chosen < MIN_FRAME_GAP_MS:
                break                             # 末帧太近：留给 emit_end 帧补
            out_t.append(chosen)
            out_ch.append(sample(chosen))
            t = chosen
        if emit_end not in out_t and (loop or emit_end == ts[-1]):
            if out_t[-1] == emit_end - MIN_FRAME_GAP_MS:
                out_t.pop(); out_ch.pop()         # 腾出 ≥20ms 间隔
            out_t.append(emit_end)
            out_ch.append(sample(emit_end))

        m2 = {**data, "tracks": {**data["tracks"], "servo": [
            {"t": tt, "ch": cc} for tt, cc in zip(out_t, out_ch)]}}
        raw = json.dumps(m2, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
        if len(raw) <= budget or cur_tol >= 8.0:
            break
        cur_tol *= 1.6                            # 超预算：放宽偏差重采

    note = (f"{len(servo)} 帧 → {len(out_t)} 帧，tol={cur_tol:.2f}°，"
            f"{len(raw)} B")
    return m2, note


# ============================ CLI ============================

def cmd_validate(path: Path) -> int:
    files = sorted(path.rglob("*.motion")) if path.is_dir() else [path]
    if not files:
        print(f"未找到 .motion 文件：{path}")
        return 2
    failed = 0
    for f in files:
        raw = f.read_bytes()
        try:
            data = json.loads(raw.decode("utf-8"))
        except (UnicodeDecodeError, json.JSONDecodeError) as e:
            print(f"[FAIL] {f.name}: JSON 解析失败 — {e}")
            failed += 1
            continue
        errors = validate_motion(data, len(raw))
        if errors:
            failed += 1
            print(f"[FAIL] {f.name}")
            for e in errors:
                print(f"       - {e}")
        else:
            print(f"[ OK ] {f.name}  ({len(raw)} B, {data['duration_ms']} ms, loop={data['loop']})")
    total = len(files)
    print(f"\n{total - failed}/{total} 通过校验")
    return 1 if failed else 0


def cmd_gen(preset: str, out: Path) -> int:
    if preset not in PRESETS:
        print(f"未知预设 {preset!r}，可选：{', '.join(PRESETS)}")
        return 2
    data = PRESETS[preset]()
    raw = json.dumps(data, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(raw)
    print(f"已生成 {out}（{len(raw)} B）")
    return 0 if not validate_motion(data, len(raw)) else 1


def cmd_beautify(infile: Path, out: Path | None, tol: float, max_gap: int,
                 budget: int) -> int:
    try:
        data = json.loads(infile.read_text(encoding="utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as e:
        print(f"[FAIL] {infile.name}: JSON 解析失败 — {e}")
        return 2
    m2, note = beautify_motion(data, tol=tol, max_gap=max_gap, budget=budget)
    raw = json.dumps(m2, ensure_ascii=False, separators=(",", ":")).encode("utf-8")
    errors = validate_motion(m2, len(raw))
    if errors:
        print(f"[FAIL] {infile.name}: 烘焙结果未过校验")
        for e in errors:
            print(f"       - {e}")
        return 1
    if out is None:
        out = infile.with_name(f"{infile.stem}_smooth{infile.suffix}")
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_bytes(raw)
    print(f"[ OK ] {infile.name} → {out.name}  {note}")
    return 0


def cmd_stats(path: Path) -> int:
    files = sorted(path.rglob("*.motion")) if path.is_dir() else [path]
    if not files:
        print(f"未找到 .motion 文件：{path}")
        return 2
    sizes = []
    for f in files:
        raw = f.read_bytes()
        try:
            d = json.loads(raw)
            loop = "循环" if d.get("loop") else "单次"
            print(f"{f.name:<16} {len(raw):>6} B  {d.get('duration_ms', 0):>7} ms  {loop}")
        except json.JSONDecodeError:
            print(f"{f.name:<16} {len(raw):>6} B  <解析失败>")
        sizes.append(len(raw))
    print(f"\n共 {len(files)} 个，平均 {sum(sizes) // len(sizes)} B，最大 {max(sizes)} B"
          f"（规范 2–10 KB，硬上限 16 KB）")
    return 0


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=".motion 动作包校验与生成工具")
    sub = p.add_subparsers(dest="cmd", required=True)
    v = sub.add_parser("validate", help="校验单个或目录下全部 .motion")
    v.add_argument("path", type=Path)
    g = sub.add_parser("gen", help="生成动作文件")
    g.add_argument("preset", choices=sorted(PRESETS))
    g.add_argument("out", type=Path)
    s = sub.add_parser("stats", help="汇总动作包数量与大小")
    s.add_argument("path", type=Path)
    b = sub.add_parser("beautify", help="缓动烘焙：关键帧样条化重采样（固件仍线性插值）")
    b.add_argument("infile", type=Path)
    b.add_argument("out", type=Path, nargs="?")
    b.add_argument("--tol", type=float, default=1.0, help="曲线-弦最大偏差（度），默认 1.0")
    b.add_argument("--max-gap", type=int, default=200, help="单帧最长跨度 ms，默认 200")
    b.add_argument("--budget", type=int, default=10240, help="字节预算，默认 10240（规范软上限）")
    args = p.parse_args(argv)

    if args.cmd == "validate":
        return cmd_validate(args.path)
    if args.cmd == "gen":
        return cmd_gen(args.preset, args.out)
    if args.cmd == "beautify":
        return cmd_beautify(args.infile, args.out, args.tol, args.max_gap, args.budget)
    return cmd_stats(args.path)


if __name__ == "__main__":
    sys.exit(main())
