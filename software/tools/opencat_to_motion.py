#!/usr/bin/env python3
"""opencat_to_motion.py — 把 Petoi OpenCat 的 instinct/动作库（.h 数组）转换成 PalmRobot .motion。

来源格式（src/skill.h dataLen/formatSkill + pyUI/SkillComposer.py 官方解析对齐）：
  const int8_t <name>[] PROGMEM = { period, pitch, roll, ratio,  帧数据... };
  period > 1  步态：header 4 + period 帧 × 8 列（WALKING_DOF，对角序）
  period == 1 姿态：header 4 + 1 帧 × DOF 列（旧固件 8 = 纯腿；master 16 = 头尾臂 4 + 腿 8 + 臂 4）
  period < 0  行为：header 7 + N 帧 × 20（时序模型不同，本工具跳过并提示）

8 腿列顺序 = 对角序（从 InstinctBittle.h 数据反推，vt 小跑对角锁步 / sit 坐姿全对称双重验证）：
  [LfS, RfS, RbS, LbS, LfK, RfK, RbK, LbK]  左右两侧同号存储（固件 mirror() 交换相邻对 = 左右互换）

值语义：相对标定零位的偏移角（度），× ratio 还原（calibratedPWM: duty = zero + angle*dir）。

到 PalmRobot 的映射（8DOF 表达不了腿轨迹，保"手势"：重心/俯仰/蹲起，同 make_dances 词汇）：
  sway  重心左右 ← (右肩均 − 左肩均)；pitch 前后倾 ← (前肩均 − 后肩均) + 膝差 ×0.5
  k     蹲起     ← 四膝均值（钳到 [0,30]，负值=伸直归零）
  帧时长：步态默认 100ms/帧（--frame-ms），姿态固定 600ms 保持（非循环）。

许可证：OpenCat 仓库为 MIT（© Rongzhong Li）。头文件由使用者自备、不入本仓库；
转换产物保留 meta.source = "opencat" 署名，分发时请附 MIT 声明。

用法（在 software/ 目录）：
  simulation/.venv/Scripts/python tools/opencat_to_motion.py ~/Downloads/InstinctBittle.h --list
  simulation/.venv/Scripts/python tools/opencat_to_motion.py InstinctBittle.h            # 全部转换
  simulation/.venv/Scripts/python tools/opencat_to_motion.py InstinctBittle.h --skills vtF,sit
  可选 --out-dir / --frame-ms / --scale / --raw（跳过缓动烘焙）
"""
from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
MOTION_GEN = HERE / "motion_gen.py"
sys.path.insert(0, str(HERE))
import motion_gen  # noqa: E402  复用缓动烘焙，避免两处逻辑漂移

STAND = [135, 45, 45, 135, 0, 180, 0, 180]
LIM = [(45, 180), (0, 135), (0, 135), (45, 180), (0, 180), (0, 180), (0, 180), (0, 180)]
POSTURE_HOLD_MS = 600
DEFAULT_FRAME_MS = 100     # 步态每帧时长；OpenCat 无显式字段，按小跑 ~1Hz 反推
DEFAULT_SCALE = 0.6        # Bittle 偏移 ±35° → PalmRobot 手势幅度


def ch(**over) -> list[int]:
    """从站立位覆盖指定通道（0=R1 1=R2 2=L1 3=L2 4=R4 5=R3 6=L3 7=L4），clip 到限位。"""
    c = list(STAND)
    for k, v in over.items():
        i = int(k[1:])
        c[i] = min(max(int(round(v)), LIM[i][0]), LIM[i][1])
    return c


def parse_header(text: str) -> dict[str, list[int]]:
    """解析 .h 里全部 const int8_t name[] PROGMEM = {...} 数组（容忍注释/换行/空项）。"""
    skills: dict[str, list[int]] = {}
    for m in re.finditer(r"const\s+int8_t\s+(\w+)\s*\[\]\s*PROGMEM\s*=\s*\{(.*?)\};",
                         text, re.S):
        body = re.sub(r"/\*.*?\*/|//.*", "", m.group(2)).replace("\n", " ")
        vals = [int(x) for x in (t for t in (s.strip() for s in body.split(",")) if t)]
        skills[m.group(1)] = vals
    return skills


def legs_of(frame: list[int]) -> list[int]:
    """从一帧（8 或 16 宽）提取 8 腿列（对角序）。"""
    if len(frame) >= 16:                      # master 固件：[头×4, 腿×8, 臂×4]
        return frame[4:12]
    return frame[:8]                          # 旧固件 / 步态帧：纯腿


def to_palm(legs: list[int], scale: float) -> list[int]:
    """OpenCat 8 腿偏移 → PalmRobot 8 通道（重心/俯仰/蹲起三手势，同 make_dances 词汇）。"""
    lf_s, rf_s, rb_s, lb_s, lf_k, rf_k, rb_k, lb_k = legs
    sway = ((rf_s - lf_s) + (rb_s - lb_s)) / 4.0            # 右腿前伸 → 重心右移
    pitch = ((lf_s + rf_s) - (lb_s + rb_s)) / 4.0 \
        + ((lf_k + rf_k) - (lb_k + rb_k)) / 4.0 * 0.5       # 前倾 → 前膝多折
    k = (lf_k + rf_k + lb_k + rb_k) / 4.0
    s = max(min(sway * scale, 26.0), -26.0)
    l = max(min(pitch * scale, 20.0), -20.0)
    kk = min(max(k * scale, 0.0), 30.0)                     # 膝只能折叠，负值=归位
    return ch(c0=STAND[0] - s + 0.4 * l, c2=STAND[2] + s - 0.4 * l,
              c1=STAND[1] - s * 0.5, c3=STAND[3] + s * 0.5,
              c4=STAND[4] + kk - l, c5=STAND[5] - kk - l,
              c6=STAND[6] + kk + l, c7=STAND[7] - kk + l)   # 左膝折叠与右膝镜像（L3 加 L4 减）


def convert(name: str, vals: list[int], frame_ms: int, scale: float) -> tuple[dict, str]:
    """单个 skill → (.motion dict, 备注)。行为序列返回 ("", 原因) 跳过。"""
    period = vals[0]
    if period < 0:
        return {}, "skip: behavior（时序模型不同，未支持）"
    ratio = vals[3]
    frames_n = abs(period)
    wide = 8 if period > 1 else len(vals) - 4
    if (len(vals) - 4) != frames_n * wide:
        return {}, f"skip: 长度不符（{(len(vals) - 4)} =? {frames_n}×{wide}）"
    rows = [vals[4 + i * wide: 4 + (i + 1) * wide] for i in range(frames_n)]

    name = f"oc_{name.lower()}"               # 动作名规范限 [a-z0-9_-]，原名存 meta
    if period == 1:                            # 姿态：保持 600ms，非循环
        angles = to_palm([v * ratio for v in legs_of(rows[0])], scale)
        servo = [{"t": 0, "ch": angles}, {"t": POSTURE_HOLD_MS, "ch": angles}]
        return ({"name": name, "version": 1, "duration_ms": POSTURE_HOLD_MS,
                 "loop": False, "tracks": {"servo": servo},
                 "meta": {"author": "palmrobot", "source": "opencat",
                          "source_skill": name[3:], "tags": ["OpenCat", "姿态"],
                          "license": "OpenCat 数据 MIT © Rongzhong Li"}}, "posture")
    servo = [{"t": i * frame_ms, "ch": to_palm([v * ratio for v in legs_of(r)], scale)}
             for i, r in enumerate(rows)]
    dur = frames_n * frame_ms
    return ({"name": name, "version": 1, "duration_ms": dur,
             "loop": True, "tracks": {"servo": servo},
             "meta": {"author": "palmrobot", "source": "opencat",
                      "source_skill": name[3:], "tags": ["OpenCat", "步态"],
                      "license": "OpenCat 数据 MIT © Rongzhong Li"}}, "gait")


def main() -> int:
    ap = argparse.ArgumentParser(description="OpenCat 动作库 → PalmRobot .motion")
    ap.add_argument("header", help="OpenCat 的 InstinctXxx.h（自备，不入仓库）")
    ap.add_argument("--out-dir", default=str(HERE / "motions"))
    ap.add_argument("--skills", help="逗号分隔白名单，默认全部")
    ap.add_argument("--frame-ms", type=int, default=DEFAULT_FRAME_MS, help="步态帧时长")
    ap.add_argument("--scale", type=float, default=DEFAULT_SCALE, help="幅度缩放")
    ap.add_argument("--raw", action="store_true", help="跳过缓动烘焙")
    ap.add_argument("--list", action="store_true", help="只列出 skill 清单")
    a = ap.parse_args()

    src = Path(a.header)
    if not src.exists():
        # 中文路径坑同 mediapipe/mujoco：C 层打不开，这里纯 Python 无碍；只是提示
        print(f"[err] 找不到 {src}")
        return 1
    skills = parse_header(src.read_text(encoding="utf-8", errors="replace"))
    if not skills:
        print("[err] 未解析到任何 const int8_t ... PROGMEM 数组")
        return 1
    if a.list:
        for n, v in skills.items():
            kind = "behavior" if v[0] < 0 else ("posture" if v[0] == 1 else "gait")
            print(f"{n:8s} {kind:8s} frames={abs(v[0])}")
        print(f"共 {len(skills)} 个")
        return 0

    want = set(a.skills.split(",")) if a.skills else None
    out_dir = Path(a.out_dir)
    out_dir.mkdir(parents=True, exist_ok=True)
    ok = True
    for n, v in skills.items():
        if want and n not in want:
            continue
        m, note = convert(n, v, a.frame_ms, a.scale)
        if not m:
            print(f"[{note.split(':')[0]}] {n}: {note}")
            continue
        if not a.raw:
            m, _ = motion_gen.beautify_motion(m)
        raw = json.dumps(m, ensure_ascii=False, separators=(",", ":"))
        path = out_dir / f"oc_{n}.motion"
        path.write_text(raw, encoding="utf-8")
        r = subprocess.run([sys.executable, str(MOTION_GEN), "validate", str(path)],
                           capture_output=True, text=True)
        good = r.returncode == 0
        ok &= good
        print(f"[{'ok ' if good else 'ERR'}] {path.name:16s} {len(raw.encode()):5d} B  "
              f"{m['duration_ms']:6d} ms  {len(m['tracks']['servo']):3d} 帧  {note}")
        if not good:
            sys.stdout.write(r.stdout)
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
