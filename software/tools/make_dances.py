#!/usr/bin/env python3
"""make_dances.py — 按机器人 8 关节结构直接编排成套舞蹈（不从视频重定向）。

关节模型（docs/20 §5.2，ch 序 R1,R2,L1,L2,R4,R3,L3,L4）：
  R1/L1 前髋、R2/L2 后髋、R3/L3 前膝、R4/L4 后膝。
  站立 [135,45,45,135,0,180,0,180]；膝在 0/180 为伸直，向中位收 = 折叠下蹲。
  R1↓L1↑ = 重心右移，R1↑L1↓ = 重心左移（dance 预设同款用法）。

"优美度"手法（对照动画十二原则的 8 自由度落地）：
  1. 脊柱波（ripple）：前膝相位领先后膝——蹲起伏不再是四腿齐动（机器感
     的最大来源就是全同步），而是一条从胸口传到后腰的波。
  2. 正交合成（Lissajous）：侧倾与俯仰相位差 90°，身体轨迹从"直线往返"
     变成圆/8 字——连续不间断的流比一段一段的动作优雅。
  3. 非对称包络：缓开急合（swan 展翅）、渐强蹲起（太极起势）——
     自然运动很少有完美对称的正弦。
  4. 对角步态词汇（d）：对角腿对 {R4+L3}/{R3+L4} 交替折叠——哺乳动物
     的对角耦合，比"双后膝同收"更像活的。
  5. 预备下蹲(anticipation) + 二次回弹(follow-through)：bounce 同款。

CPG（参数化振荡器）动作：idle 待机呼吸 / bounce 蹦跳——
  呼吸微起伏、预备下蹲、二次回弹，"永远不完全静止"是生命感的最低成本来源。

编排舞蹈：tai_chi 太极 / swan 天鹅舞 / cha_cha 蹦恰恰 /
  waltz 华尔兹（3/4 拍 rise-and-fall + 身体画圆）/ moonflow 月光流（慢速 8 字）/
  yoga 猫式伸展（对角交替拉伸）。

互动情绪动作：yes 点头 / no 摇头 / happy 开心 / act_cute 撒娇 /
  startled 惊吓 / bow 鞠躬 / greet_show 开机欢迎——8 自由度能做的
  "表情动作"词汇：俯仰点头、髋摆摇头、蹲起伏跳、侧倾撒娇、缩蹲惊吓。

所有动作默认经 motion_gen.beautify 缓动烘焙后落盘（固件保持线性插值）。

用法：
  .venv/Scripts/python make_dances.py            # 生成 15 套到 tools/motions/
  .venv/Scripts/python make_dances.py --raw      # 跳过缓动烘焙
"""
from __future__ import annotations

import argparse
import json
import math
import subprocess
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
OUT = HERE / "motions"
MOTION_GEN = HERE / "motion_gen.py"
sys.path.insert(0, str(HERE))
import motion_gen  # noqa: E402  缓动烘焙复用同一实现，避免两处逻辑漂移

STAND = [135, 45, 45, 135, 0, 180, 0, 180]
LIM = [(45, 180), (0, 135), (0, 135), (45, 180), (0, 180), (0, 180), (0, 180), (0, 180)]
FACE_HAPPY = [80, 10, 60, 40, 30, 90, 60, 50, 0, 0, 40, 80]
FACE_CALM = [60, 0, 40, 20, 10, 60, 40, 50, 0, 0, 20, 40]   # 太极用：平和脸
FACE_SURPRISED = [95, 0, 85, 20, 55, 35, 70, 85, 0, 0, 15, 95]  # 惊吓：瞪眼挑眉张嘴


def ch(**over) -> list[int]:
    """从站立位覆盖指定通道（0=R1 1=R2 2=L1 3=L2 4=R4 5=R3 6=L3 7=L4），clip 到限位。"""
    c = list(STAND)
    for k, v in over.items():
        i = int(k[1:])                       # c0=ch0, c5=ch5...
        c[i] = min(max(int(round(v)), LIM[i][0]), LIM[i][1])
    return c


def pose(s: float = 0.0, k: float = 0.0, l: float = 0.0,
         kf: float | None = None, kr: float | None = None, d: float = 0.0) -> list[int]:
    """身体词汇 → 8 通道（ MuJoCo 实测校准的折叠方向）。

    s 重心左右（>0 偏右，前髋反向摆 + 后髋半幅随动）
    k 四膝同步折叠量（膝只能向中位收 = 下蹲；R4/L3 从 0 加、R3/L4 从 180 减）
    kf/kr 前/后膝各自的折叠量（缺省都 = k）——分开给才能做出"脊柱波"
    l 前后俯仰（>0 前倾：前膝多折、后膝伸展）
    d 对角折叠（>0 折 右后+左前 对角，<0 折 右前+左后；哺乳动物的对角耦合）
    """
    c = ch(c0=STAND[0] - s + 0.4 * l, c2=STAND[2] + s - 0.4 * l,
           c1=STAND[1] - s * 0.5, c3=STAND[3] + s * 0.5,
           c4=STAND[4] + (k if kr is None else kr) - l,
           c5=STAND[5] - (k if kf is None else kf) - l,
           c6=STAND[6] + (k if kf is None else kf) + l,
           c7=STAND[7] - (k if kr is None else kr) + l)
    if d > 0:                                     # 折 右后(R4)+左前(L3)
        c[4] = min(int(c[4] + d), LIM[4][1]); c[6] = min(int(c[6] + d), LIM[6][1])
    elif d < 0:                                   # 折 右前(R3)+左后(L4)
        c[5] = max(int(c[5] + d), LIM[5][0]); c[7] = max(int(c[7] + d), LIM[7][0])
    return c


def ripple(t: float, base_k: float, amp: float, period_s: float,
           lead_s: float = 0.18) -> tuple[float, float]:
    """脊柱波：同一膝起伏函数按时间错位取前/后两份（前膝领先）。

    前膝先蹲后膝跟上 → 身体出现"从胸口传到后腰"的波，
    消掉四腿齐动带来的机械感。返回 (kf, kr)。
    """
    ph = 2 * math.pi / period_s
    w = lambda tt: base_k + amp * math.sin(ph * tt)          # noqa: E731
    return w(t + lead_s), w(t - lead_s)


def build(name: str, duration_ms: int, loop: bool, frames: list[tuple[int, list[int]]],
          face: list[int] | list[tuple[int, list[int]]] | None, tags: list[str]) -> dict:
    servo = [{"t": t, "ch": c} for t, c in frames]
    m: dict = {"name": name, "version": 1, "duration_ms": duration_ms, "loop": loop,
               "tracks": {"servo": servo}}
    if face:
        if isinstance(face[0], int):            # 单表情：t=0 一帧
            face = [(0, face)]                  # type: ignore[assignment]
        m["tracks"]["face"] = [{"t": t, "expr": e} for t, e in face]  # type: ignore[union-attr]
    m["meta"] = {"author": "palmrobot", "source": "choreography", "tags": tags,
                 "created": "2026-09-21"}
    return m


def wave(t: float, period_s: float, phase: float = 0.0) -> float:
    """[-1,1] 正弦，t 秒。"""
    return math.sin(2 * math.pi * t / period_s + phase)


# ---------------- 太极：起势(蹲起) → 云手(画圆) → 揽雀尾(前倾后坐) → 收势 ----------------

def tai_chi() -> dict:
    frames: list[tuple[int, list[int]]] = []
    D = 12.0                      # 12s 一循环
    step = 0.15
    ts = [round(i * step, 3) for i in range(int(D / step))] + [D]

    def win(t: float, a: float, b: float) -> float:
        """sin² 软窗口：段内 0→1→0，端点斜率为零（段与段速度连续）。"""
        return math.sin(math.pi * min(max((t - a) / (b - a), 0.0), 1.0)) ** 2

    def gen(t: float) -> list[int]:
        # 起势：0-4s 两蹲两起，幅度渐强（0.45→1）——对称的重复是机器，渐强是呼吸
        if t <= 4.0:
            ramp = 0.45 + 0.55 * t / 4.0
            squat = ramp * (1 - math.cos(2 * math.pi * t / 2.0)) / 2
        else:
            squat = 0.12 + 0.12 * math.sin(2 * math.pi * (t - 4.0) / 4.0)
        # 云手：4-9s 侧倾与俯仰相位差 90°（sin² 软窗）→ 重心画椭圆而非直线往返
        w = win(t, 3.5, 9.0)
        ph = 2 * math.pi * (t - 3.5) / 5.5 * 2
        sway = w * math.sin(ph)
        lean = 0.55 * w * math.cos(ph)                     # 椭圆的短轴
        # 揽雀尾：9-11.5s 前倾→后坐一个来回（软窗衔接）
        lean += win(t, 9.0, 11.5) * math.sin(2 * math.pi * (t - 9.0) / 2.5)
        squat = min(1.0, squat + 0.10 * abs(lean))          # 倾时略沉
        kf, kr = ripple(t, 26 * squat, 2.2, 2.0)            # 脊柱波：周期同起势
        return pose(s=20 * sway, kf=kf, kr=kr, l=14 * lean)

    for t in ts:
        frames.append((round(t * 1000), gen(t)))
    return build("tai_chi", int(D * 1000), True, frames, FACE_CALM, ["太极", "慢速"])


# ---------------- 天鹅舞：踮脚起伏 → 展翅 → 对角碎步 → 单侧压低回旋 ----------------

def swan() -> dict:
    frames = []
    D = 4.8                       # 4 小节 × 1.2s
    step = 0.1
    ts = [round(i * step, 3) for i in range(int(D / step))] + [D]

    def gen(t: float) -> list[int]:
        bar = int(t // 1.2)                       # 当前小节
        bt = t - bar * 1.2                        # 小节内时间
        k = 10 + 8 * (1 - abs(2 * bt / 1.2 - 1))  # 踮脚起伏（全曲伴随）
        kf, kr = ripple(t, k, 1.5, 1.2)           # 脊柱波随小节起伏
        if bar == 0:                              # 小节1：直立踮脚，两次起伏
            return pose(kf=kf, kr=kr)
        if bar == 1:                              # 小节2：展翅——缓开急合（非对称包络）
            u = (bt / 1.2) ** 1.5                 # p>1：前 3/4 时间缓缓打开
            w = math.sin(math.pi * u)
            return pose(s=22 * w, kf=kf, kr=kr, l=4 * w)   # 挺胸配合开翅
        if bar == 2:                              # 小节3：对角碎步（对角对交替微折）
            half = 1.0 if bt % 0.6 < 0.3 else -1.0
            return pose(s=12 * half, kf=kf, kr=kr, d=7 * half)
        # 小节4：缓缓沉向右前成"天鹅回首"，末 0.2s 回正
        if bt < 1.0:
            u = min(max((bt - 0.05) / 0.5, 0.0), 1.0)
            u = u * u * (3 - 2 * u)               # smoothstep 沉入
            c = pose(s=14 * u, k=10 + 16 * u)
            c[0] -= round(4 * u); c[2] -= round(4 * u)     # 前髋再收：头颈压向右前
            return c
        return list(STAND)

    for t in ts:
        frames.append((round(t * 1000), gen(t)))
    return build("swan", int(D * 1000), True, frames, FACE_HAPPY, ["天鹅舞", "4拍"])


# ---------------- 蹦恰恰：快快慢 × 2，重拍顶胯下压 ----------------

def cha_cha() -> dict:
    # 节奏：快300 快300 慢1000 × 2 = 3.2s
    beats = [(0.3, "L"), (0.3, "R"), (1.0, "S"),
             (0.3, "R"), (0.3, "L"), (1.0, "S")]
    frames: list[tuple[int, list[int]]] = [(0, list(STAND))]   # 规范：首帧 t=0 定义初始姿态
    t = 0.0
    for dur, kind in beats:
        n = 2 if kind in ("L", "R") else 4        # 快拍 2 帧、慢拍 4 帧
        for j in range(n):
            ft = t + dur * (j + 1) / n            # 帧时间（拍末）
            p = (j + 1) / n                       # 拍内进度 0→1
            if kind == "L":                       # 快拍：重心左
                s, k, l, td = -26 * p, 14 * p, 0.0, 0.0
            elif kind == "R":                     # 快拍：重心右
                s, k, l, td = 26 * p, 14 * p, 0.0, 0.0
            else:                                 # 慢拍：顶胯下压弹起 + 髋画小圆
                k = 30 * math.sin(math.pi * p)    # 蹲下→弹起
                s = 8 * math.sin(2 * math.pi * p)
                l = 6 * math.cos(2 * math.pi * p) # 与 s 相位差 90°：直线变圆
                td = 0.04                         # 脊柱波：蹲起有传导感
            kf, kr = ripple(t + ft, k, 2.0, 1.6, lead_s=td) if td else (k, k)
            frames.append((round(ft * 1000), pose(s=s, kf=kf, kr=kr, l=l)))
        t += dur
    return build("cha_cha", round(t * 1000), True, frames, FACE_HAPPY, ["蹦恰恰", "快快慢"])


# ---------------- CPG 生命感动作：待机呼吸 / 蹦跳 ----------------

def ss(x: float) -> float:
    """smoothstep：0→1 三阶缓动过渡。"""
    x = min(max(x, 0.0), 1.0)
    return x * x * (3 - 2 * x)


def idle() -> dict:
    """待机呼吸：膝 2~4.5° 慢起伏 + 重心 ±1.5° 极缓游移，3.2s 循环。

    生命感的关键不是动得多，而是永远不完全静止（呼吸微动作）。
    呼吸有"传导"：前膝先吸、后膝晚 0.3s 跟上——气息从胸口流向后腰，
    四腿齐动就是机械风箱，错位了才像趴着睡觉的小兽。
    """
    D = 3.2
    step = 0.1
    ts = [round(i * step, 2) for i in range(int(D / step))] + [D]
    frames = []
    for t in ts:
        b = lambda dt: (1 - math.cos(2 * math.pi * (t + dt) / D)) / 2  # noqa: E731
        kf = 2.0 + 2.5 * b(+0.3)                               # 前膝先吸
        kr = 2.0 + 2.5 * b(-0.3)                               # 后膝晚 0.3s 跟上
        s = 1.5 * math.sin(2 * math.pi * t / D)                # 重心游移，同周期闭合
        frames.append((round(t * 1000), pose(s=s, kf=kf, kr=kr)))
    return build("idle", int(D * 1000), True, frames, FACE_CALM, ["待机", "呼吸"])


def bounce() -> dict:
    """蹦跳：150 BPM，每拍一跳。

    动画十二原则的机器人版：预备下蹲（anticipation）→ 快速蹬伸 →
    二次回弹（follow-through）→ 归位。没有预备和回弹的起伏像电梯，
    有了才像活的。膝只能折叠（站立位在限位端点），"跳"表现为有节奏的弹蹲。
    """
    BEAT = 0.4                            # 150 BPM
    D = BEAT * 4                          # 1.6s 一循环
    step = 0.05
    ts = [round(i * step, 2) for i in range(int(D / step))] + [D]

    def fold(p: float) -> float:
        """拍内相位 p∈[0,1] → 膝折叠量（度）。"""
        if p < 0.35:                      # 预备下蹲（快蹲）
            return 16 * ss(p / 0.35)
        if p < 0.55:                      # 蹬伸释放（更快）
            return 16 - 14.5 * ss((p - 0.35) / 0.2)      # → 1.5
        if p < 0.8:                       # 二次回弹
            return 1.5 + 4.5 * math.sin(math.pi * (p - 0.55) / 0.25)
        return 1.5 * (1 - ss((p - 0.8) / 0.2))           # → 0 归位

    frames = []
    for t in ts:
        beat = int(t // BEAT) % 2
        p = (t % BEAT) / BEAT
        k = fold(p)
        side = 1.0 if beat == 0 else -1.0          # 左右拍交替侧倾
        s = 3.0 * side * ss(p / 0.35) * (1 - ss(max((p - 0.55) / 0.3, 0.0)))
        frames.append((round(t * 1000), pose(s=s, k=k)))
    return build("bounce", int(D * 1000), True, frames, FACE_HAPPY, ["蹦跳", "150BPM"])


# ---------------- 优美向编排：华尔兹 / 月光流 / 猫式伸展 ----------------

def waltz() -> dict:
    """华尔兹：3/4 拍 120 BPM，蓬-恰-恰，4 小节循环。

    华尔兹的灵魂是 rise and fall：强拍沉、弱拍升，膝起伏走余弦没有平顶。
    重心绕小椭圆（侧倾与俯仰相位差 90°）是"带着舞伴绕场"的错觉；
    前膝起落领先/落后 ±0.15s 让起伏沿身体传导。全部分量周期整除 6s，闭合。
    """
    BAR = 1.5                                  # 120 BPM 三拍子
    D = BAR * 4
    step = 0.08
    ts = [round(i * step, 3) for i in range(int(D / step))] + [D]
    frames = []
    for t in ts:
        rise = lambda dt: 8 + 6 * math.cos(2 * math.pi * (t + dt) / BAR)  # noqa: E731
        kf, kr = rise(+0.15), rise(-0.15)      # 强拍沉、弱拍升，前后膝错位
        s = 15 * math.sin(2 * math.pi * t / (BAR * 2))
        l = 9 * math.sin(2 * math.pi * t / (BAR * 2) - math.pi / 2)   # 90° 相位 → 椭圆
        frames.append((round(t * 1000), pose(s=s, kf=kf, kr=kr, l=l)))
    return build("waltz", int(D * 1000), True, frames, FACE_CALM, ["华尔兹", "3/4拍"])


def moonflow() -> dict:
    """月光流：8s 一笔画的慢速 8 字，冥想向。

    侧倾 ±20°（8s 基频）与俯仰 ±11°（倍频）合成 Lissajous 8 字——
    身体轨迹连续不间断，没有"一段一段"的动作边界，这是优雅的最短路径。
    膝随俯仰微沉，前膝领先 0.35s 传播波动。所有周期整除 8s，闭合。
    """
    D = 8.0
    w0 = 2 * math.pi / D
    step = 0.1
    ts = [round(i * step, 2) for i in range(int(D / step))] + [D]
    frames = []
    for t in ts:
        s = 20 * math.sin(w0 * t)
        l = 11 * math.sin(2 * w0 * t)
        def knee(dt: float) -> float:
            return (5 + 3.5 * math.sin(w0 * (t + dt) + math.pi / 2)
                    + 0.30 * abs(11 * math.sin(2 * w0 * (t + dt))))
        frames.append((round(t * 1000), pose(s=s, kf=knee(+0.35), kr=knee(-0.35), l=l)))
    return build("moonflow", int(D * 1000), True, frames, FACE_CALM, ["月光流", "慢速", "8字"])


def yoga() -> dict:
    """猫式伸展：对角腿对轮流"缓缓压低—保持—收回"，像猫伸懒腰。

    对角折叠（d 词汇）是猫科共用的身体语言：重心沉向一条对角线，
    另一条被拉长；重心随沉入侧偏移才像"压"进去。保持段叠 0.8s
    呼吸颤（±1.2°）——伸到头还要再抻一点，是拉伸的表情。6.4s 循环。
    """
    D = 6.4
    HALF = 3.2
    step = 0.08
    ts = [round(i * step, 3) for i in range(int(D / step))] + [D]

    def env(p: float) -> float:
        """单侧拉伸包络：0.35 缓入 → 保持 → 0.35 缓出。"""
        return ss(min(p / 0.35, 1.0)) * (1 - ss(min(max((p - 0.65) / 0.35, 0.0), 1.0)))

    frames = []
    for t in ts:
        p = (t % HALF) / HALF
        sign = 1.0 if (t % (2 * HALF)) < HALF else -1.0
        e = env(p)
        trem = 1.5 * math.sin(2 * math.pi * t / 0.8) * e    # 0.8s 整除 6.4，闭合
        d = sign * (24 * e + trem)
        s = -sign * 14 * e                                  # 沉向被压的对角
        l = sign * 5 * e                                    # 俯身配合：探向拉伸侧
        frames.append((round(t * 1000), pose(s=s, k=4 + 3 * e, d=d, l=l)))
    return build("yoga", int(D * 1000), True, frames, FACE_CALM, ["猫式伸展", "对角", "慢速"])


# ---------------- 互动情绪动作：点头 / 摇头 / 开心 / 撒娇 / 惊吓 / 鞠躬 / 开机欢迎 ----------------

def yes() -> dict:
    """点头：俯仰两个来回，前膝随压——"对对对"的肯定回应。"""
    D = 2.6
    step = 0.1
    ts = [round(i * step, 2) for i in range(int(D / step))] + [D]
    frames = []
    for t in ts:
        l = 12 * math.sin(2 * math.pi * t / 1.3)               # 俯仰两循环，闭合
        k = 5 + 6 * max(l, 0.0) / 12                            # 前倾时膝随压
        frames.append((round(t * 1000), pose(k=k, l=l)))
    return build("yes", int(D * 1000), True, frames, FACE_HAPPY, ["互动", "点头"])


def no() -> dict:
    """摇头：髋部快速小幅左右摆三来回——"不不不"的否定回应。"""
    D = 2.1
    step = 0.07
    ts = [round(i * step, 2) for i in range(int(D / step))] + [D]
    frames = []
    for t in ts:
        s = 10 * math.sin(2 * math.pi * t / 0.7)                # 三个来回，闭合
        k = 3 + 2 * abs(s) / 10
        frames.append((round(t * 1000), pose(s=s, k=k)))
    return build("no", int(D * 1000), True, frames, FACE_CALM, ["互动", "摇头"])


def happy() -> dict:
    """开心：两记轻快弹跳 + 左右小扭（被夸奖 / 连上的反应）。"""
    D = 1.6                                     # 两拍 × 0.8s
    step = 0.05
    ts = [round(i * step, 2) for i in range(int(D / step))] + [D]
    frames = []
    for t in ts:
        p = (t % 0.8) / 0.8
        k = 14 * math.sin(math.pi * min(p * 1.4, 1.0)) ** 0.7   # 快蹲快弹，拍内闭合
        s = 5 * math.sin(2 * math.pi * t / 0.8)                  # 扭动，整周期闭合
        frames.append((round(t * 1000), pose(s=s, k=k)))
    return build("happy", int(D * 1000), True, frames, FACE_HAPPY, ["互动", "开心"])


def act_cute() -> dict:
    """撒娇：压向一侧、前膝小碎抖着"蹭"，再换另一边。"""
    D = 3.0
    step = 0.06
    ts = [round(i * step, 2) for i in range(int(D / step))] + [D]
    frames = []
    for t in ts:
        side = math.sin(2 * math.pi * t / D)                    # −1..1，闭合
        k = 7 + 3 * math.sin(2 * math.pi * t / 0.75)            # 碎抖 0.75s 整除 3.0，闭合
        c = pose(s=13 * side, k=k)
        c[5] -= int(6 * max(side, 0.0))                         # 压右时右前膝多收（蹭）
        c[6] += int(6 * max(-side, 0.0))                        # 压左时左前膝多收
        frames.append((round(t * 1000), c))
    return build("act_cute", int(D * 1000), True, frames, FACE_HAPPY, ["互动", "撒娇"])


def startled() -> dict:
    """惊吓：瞬间缩蹲 → 带小抖僵住 → 左右探头张望 → 缓过来。

    惊吓的反应要快（80ms 蹲到位）才像反射；抖是"吓懵"的关键细节。
    """
    frames: list[tuple[int, list[int]]] = [(0, list(STAND))]
    for t in range(120, 201, 40):                               # 快速缩蹲
        frames.append((t, pose(k=8 + 18 * (t - 120) / 80)))
    for i, t in enumerate(range(250, 701, 50)):                 # 僵住，±1.5° 发抖
        frames.append((t, pose(k=26 + 1.5 * (1 if i % 2 else -1))))
    frames += [(750, pose(s=10, k=16)), (950, pose(s=-10, k=16)),   # 左右探头
               (1150, pose(k=10)), (1400, pose(k=4)),
               (1700, list(STAND)), (1800, list(STAND))]
    return build("startled", 1800, False, frames, FACE_SURPRISED, ["互动", "惊吓"])


def bow() -> dict:
    """鞠躬：预备微沉 → 俯身致意 → 保持 → 起身（演出谢幕 / 见面问候）。"""
    frames = [(0, list(STAND)),
              (300, pose(k=8)),                 # 预备（anticipation）
              (800, pose(k=16, l=16)),          # 俯身
              (1500, pose(k=16, l=16)),         # 保持致意
              (2000, pose(k=4, l=3)), (2400, list(STAND))]
    return build("bow", 2400, False, frames, FACE_HAPPY, ["互动", "鞠躬"])


def greet_show() -> dict:
    """开机欢迎：站定亮相 → 挥手 → 鞠躬 → 弹跳两下 → 扭一扭 → 归位（单次演出）。"""
    D = 8.0

    def at(t: float) -> list[int]:
        if t < 0.6:                                             # 站定亮相
            return pose()
        if t < 2.4:                                             # 挥手（wave 预设同款词汇）
            u = (t - 0.6) / 1.8
            lift = ss(u / 0.2) if u < 0.2 else (1 - ss((u - 0.85) / 0.15) if u > 0.85 else 1.0)
            c = pose(k=2)
            c[0] = STAND[0] - 20 * lift \
                + 15 * math.sin(2 * math.pi * (t - 0.6) / 0.6) * lift
            c[5] = STAND[5] - 30 * lift
            return c
        if t < 4.2:                                             # 鞠躬
            u = (t - 2.4) / 1.8
            d = ss(min(u / 0.3, 1.0)) * (1 - ss(max((u - 0.7) / 0.3, 0.0)))
            return pose(k=4 + 12 * d, l=16 * d)
        if t < 6.0:                                             # 弹跳两下
            p = ((t - 4.2) % 0.9) / 0.9
            return pose(k=13 * math.sin(math.pi * min(p * 1.4, 1.0)) ** 0.7,
                        s=4 * math.sin(2 * math.pi * (t - 4.2) / 0.9))
        if t < 7.3:                                             # 开心扭动
            u = t - 6.0
            env = ss(min(u / 0.2, 1.0)) * (1 - ss(max((u - 1.1) / 0.2, 0.0)))
            return pose(s=12 * env * math.sin(2 * math.pi * u / 0.65), k=5)
        return pose(k=5 * (1 - ss((t - 7.3) / 0.7)))            # 归位

    frames = [(round(t * 1000), at(round(t, 1)))
              for t in [round(i * 0.1, 1) for i in range(int(D / 0.1))]]
    frames.append((int(D * 1000), list(STAND)))
    face = [(0, FACE_CALM), (600, FACE_HAPPY), (7300, FACE_CALM)]
    return build("greet_show", int(D * 1000), False, frames, face, ["演出", "开机欢迎"])


# ---------------- 输出 ----------------

def main() -> int:
    ap = argparse.ArgumentParser(description="按 8 关节结构编排成套舞蹈")
    ap.add_argument("--raw", action="store_true", help="跳过缓动烘焙（输出原始关键帧）")
    args = ap.parse_args()

    OUT.mkdir(parents=True, exist_ok=True)
    dances = {"tai_chi": tai_chi(), "swan": swan(), "cha_cha": cha_cha(),
              "waltz": waltz(), "moonflow": moonflow(), "yoga": yoga(),
              "idle": idle(), "bounce": bounce(),
              "yes": yes(), "no": no(), "happy": happy(), "act_cute": act_cute(),
              "startled": startled(), "bow": bow(), "greet_show": greet_show()}
    ok = True
    for name, m in dances.items():
        n_raw = len(m["tracks"]["servo"])
        note = ""
        if not args.raw:
            m, note = motion_gen.beautify_motion(m)
            note = f"  [烘焙 {note}]"
        raw = json.dumps(m, ensure_ascii=False, separators=(",", ":"))
        path = OUT / f"{name}.motion"
        path.write_text(raw, encoding="utf-8")
        print(f"[gen] {path.name}  {len(raw.encode())} B  {m['duration_ms']} ms  "
              f"{n_raw}→{len(m['tracks']['servo'])} 帧  loop={m['loop']}{note}")
        r = subprocess.run([sys.executable, str(MOTION_GEN), "validate", str(path)],
                           capture_output=True, text=True)
        sys.stdout.write(r.stdout)
        if r.returncode != 0:
            ok = False
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
