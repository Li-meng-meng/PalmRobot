#!/usr/bin/env python3
"""sim_server.py — MuJoCo 物理仿真服务：让上位机像连真机一样连"虚拟引擎"。

两个本机端口：
  127.0.0.1:8555  指令通道，docs/20 行协议（READY 横幅 / 严格请求-应答 / EVT）
  127.0.0.1:8556  视频流通道，5 Hz JPEG，每帧 4 字节大端长度前缀 + JPEG 字节

物理仿真与 motion_player.py 同款语义：
  - FRAME/POSE 角度 → ctrl（弧度域 == 舵机 0-180° 域）
  - POSE wave/dance 与 PLAY <name> 播放 tools/motions/*.motion（固件同款线性插值）
  - MOVE v w → 循环播放 walk.motion；STOP → 回站立位
  - 摔倒自动复位站立（仿真调试用途，恢复演示不中断）

用法：
  .venv/Scripts/python sim_server.py            # 前台运行，Ctrl+C 退出
  .venv/Scripts/python sim_server.py --no-video # 只开指令通道
"""
from __future__ import annotations

import json
import math
import re
import socket
import struct
import sys
import threading
import time
from pathlib import Path

import mujoco
import numpy as np

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE))
from sim_load import load_model, JOINT_ORDER  # noqa: E402

TOOLS_MOTIONS = HERE.parent / "tools" / "motions"

CMD_PORT = 8555
VID_PORT = 8556
FW_VERSION = "v0.1.0-sim"

# 摔倒判定（与 motion_player.py 一致）
FALL_HEIGHT = 0.02
FALL_TILT_DEG = 45.0
FALL_CONFIRM_S = 0.3

# 站立/坐下/睡眠姿态表（与 PalmRobot.Core ChannelMap 冻结值一致）
POSES: dict[str, list[int]] = {
    "stand": [135, 45, 45, 135, 0, 180, 0, 180],
    "sit":   [135, 75, 45, 75, 50, 170, 20, 170],
    "sleep": [135, 80, 45, 80, 60, 160, 20, 160],
}

# 视频流参数
VID_FPS = 5
VID_W, VID_H = 320, 240


# ---------------------------------------------------------------------------
# 共享状态（指令线程写，物理线程读；一把锁足够）

class SimState:
    def __init__(self) -> None:
        self.lock = threading.Lock()
        self.mode = "manual"                 # manual | motion
        self.manual_angles: list[float] = list(POSES["stand"])
        self.motion: dict | None = None      # .motion JSON dict
        self.motion_name = ""
        self.motion_loop = False
        self.motion_t0 = 0.0                 # perf_counter 起点
        self.motion_done_sent = False
        self.pose = "stand"                  # STATUS 里报的姿态名
        self.fallen = False

    def set_manual(self, angles: list[float], pose: str) -> None:
        with self.lock:
            self.mode = "manual"
            self.manual_angles = list(angles)
            self.pose = pose
            self.motion = None
            self.fallen = False

    def play(self, motion: dict, name: str) -> None:
        with self.lock:
            self.mode = "motion"
            self.motion = motion
            self.motion_name = name
            self.motion_loop = bool(motion.get("loop"))
            self.motion_t0 = time.perf_counter()
            self.motion_done_sent = False
            self.pose = "motion"
            self.fallen = False


STATE = SimState()


def load_motion_file(name: str) -> dict | None:
    """按名字加载 tools/motions/<name>.motion（PLAY / POSE wave|dance 用）。"""
    path = TOOLS_MOTIONS / f"{name}.motion"
    if not path.is_file():
        return None
    try:
        m = json.loads(path.read_text(encoding="utf-8"))
        if m.get("version") != 1 or not m.get("tracks", {}).get("servo"):
            return None
        return m
    except (json.JSONDecodeError, OSError):
        return None


def sample_motion(m: dict, t_ms: float) -> list[float]:
    """固件同款插值（motion_player.sample_motion 同源）：线性关键帧 + loop 回绕。"""
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


# ---------------------------------------------------------------------------
# 视频流：5 Hz JPEG，4 字节大端长度前缀

class VideoHub:
    def __init__(self) -> None:
        self.clients: list[socket.socket] = []
        self.lock = threading.Lock()

    def add(self, sock: socket.socket) -> None:
        with self.lock:
            self.clients.append(sock)
        print(f"[vid] 视频客户端接入（当前 {len(self.clients)}）", flush=True)

    def broadcast(self, jpeg: bytes) -> None:
        frame = struct.pack(">I", len(jpeg)) + jpeg
        with self.lock:
            dead = []
            for s in self.clients:
                try:
                    s.sendall(frame)
                except OSError:
                    dead.append(s)
            for s in dead:
                self.clients.remove(s)
        if dead:
            print(f"[vid] 移除 {len(dead)} 个断开的视频客户端", flush=True)

    def close_all(self) -> None:
        with self.lock:
            for s in self.clients:
                try:
                    s.close()
                except OSError:
                    pass
            self.clients.clear()


def serve_video(port: int, hub: VideoHub) -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(4)
    print(f"[vid] 视频流就绪 tcp://127.0.0.1:{port}（{VID_FPS} Hz JPEG）", flush=True)
    while True:
        try:
            sock, addr = srv.accept()
            sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
            hub.add(sock)
        except OSError:
            break


# ---------------------------------------------------------------------------
# 指令通道：docs/20 行协议

def handle_command(line: str) -> list[str]:
    """返回应答行列表（可能含异步 EVT，按顺序发出）。"""
    tokens = line.split()
    if not tokens:
        return []
    cmd = tokens[0].upper()

    if cmd == "PING":
        return ["PONG"]

    if cmd == "VER":
        return [f"OK {FW_VERSION}"]

    if cmd == "STATUS":
        with STATE.lock:
            body = (f'{{"v":12.4,"t":45,"batt":80,"pose":"{STATE.pose}","err":0}}')
        return [f"OK {body}"]

    if cmd == "POSE":
        name = tokens[1].lower() if len(tokens) == 2 else ""
        if name in POSES:
            STATE.set_manual(POSES[name], name)
            return ["OK"]
        if name in ("wave", "dance"):
            m = load_motion_file(name)
            if m is None:
                return ["ERR 8 NOT_FOUND"]
            STATE.play(m, name)
            return ["OK"]
        return ["ERR 1 BAD_ARGS"]

    if cmd == "STOP":
        STATE.set_manual(POSES["stand"], "stand")
        return ["OK"]

    if cmd == "MOVE":
        if len(tokens) == 3 and _is_int(tokens[1]) and _is_int(tokens[2]) \
                and -100 <= int(tokens[1]) <= 100 and -100 <= int(tokens[2]) <= 100:
            m = load_motion_file("walk")
            if m is None:
                return ["ERR 8 NOT_FOUND"]
            STATE.play(m, "walk")
            return ["OK"]
        return ["ERR 1 BAD_ARGS"]

    if cmd == "FRAME":
        # FRAME t a1..a8（固件按 docs/20 忽略帧序号，直接到位）
        if len(tokens) == 10 and _is_int(tokens[1]) and all(_is_int(t) for t in tokens[2:]):
            angles = [float(t) for t in tokens[2:]]
            STATE.set_manual(angles, "motion")
            return ["OK"]
        return ["ERR 1 BAD_ARGS"]

    if cmd == "PLAY":
        name = tokens[1] if len(tokens) == 2 else ""
        if name == "loop":
            return ["ERR 1 BAD_ARGS"]
        m = load_motion_file(name)
        if m is None:
            return ["ERR 8 NOT_FOUND"]
        STATE.play(m, name)
        return ["OK"]

    if cmd == "PUTM":
        # docs/20 §5.7：OK PUT_READY → 紧跟 size 字节 UTF-8 JSON → OK SAVED
        if len(tokens) == 3 and _is_int(tokens[2]) and 1 <= int(tokens[2]) <= 16384:
            global UPLOAD_PENDING
            UPLOAD_PENDING = {"name": tokens[1], "size": int(tokens[2])}
            return ["OK PUT_READY"]
        return ["ERR 1 BAD_ARGS"]

    if cmd == "LISTM":
        out = []
        for p in sorted(TOOLS_MOTIONS.glob("*.motion")):
            out.append(f"OK {p.stem} {p.stat().st_size}")
        out.append("OK DONE")
        return out

    if cmd == "RESET":
        reset_to_stand()
        return ["OK"]

    if cmd == "REBOOT":
        reset_to_stand()
        return ["OK", f"READY {FW_VERSION}"]

    return ["ERR 2 UNKNOWN_CMD"]


def _is_int(s: str) -> bool:
    try:
        int(s)
        return True
    except ValueError:
        return False


# PUTM 声明（docs/20 §5.7）：handle_command 置位，client_session 收满 size 字节后落盘
UPLOAD_PENDING: dict | None = None


def save_uploaded_motion(name: str, raw: bytes) -> str:
    """校验并保存上传的 .motion（与样例同目录，PLAY <name> 即可播放），返回应答行。"""
    if not re.fullmatch(r"[A-Za-z0-9_\-]{1,32}", name):
        return "ERR 1 BAD_ARGS"
    try:
        text = raw.decode("utf-8")
        m = json.loads(text)
        if m.get("version") != 1 or not m.get("tracks", {}).get("servo"):
            return "ERR 11 CHECKSUM"
    except (json.JSONDecodeError, UnicodeDecodeError):
        return "ERR 11 CHECKSUM"
    (TOOLS_MOTIONS / f"{name}.motion").write_text(text, encoding="utf-8")
    print(f"[cmd] 动作包已保存: {name}（{len(raw)} B）", flush=True)
    return "OK SAVED"


def reset_to_stand() -> None:
    STATE.set_manual(POSES["stand"], "stand")
    RESET_FLAG_holder.append(time.time())   # 物理线程轮询该标记并复位


# 物理线程复位标记：非空即请求复位（摔倒恢复 / RESET / REBOOT）
RESET_FLAG_holder: list[float] = []


def serve_commands(port: int) -> None:
    srv = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    srv.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
    srv.bind(("127.0.0.1", port))
    srv.listen(4)
    print(f"[cmd] 指令通道就绪 tcp://127.0.0.1:{port}（docs/20 行协议）", flush=True)
    while True:
        try:
            sock, addr = srv.accept()
        except OSError:
            break
        print(f"[cmd] 客户端接入 {addr[0]}:{addr[1]}", flush=True)
        threading.Thread(target=client_session, args=(sock,), daemon=True).start()


def client_session(sock: socket.socket) -> None:
    global UPLOAD_PENDING
    CMD_CLIENTS.append(sock)
    try:
        sock.setsockopt(socket.IPPROTO_TCP, socket.TCP_NODELAY, 1)
        sock.sendall(f"READY {FW_VERSION}\n".encode())
        buf = b""
        while True:
            chunk = sock.recv(4096)
            if not chunk:
                break
            buf += chunk
            # PUTM 数据体：收满声明的 size 字节（原始字节，不走行切分）
            if UPLOAD_PENDING is not None:
                need = UPLOAD_PENDING["size"]
                while len(buf) < need:
                    chunk = sock.recv(min(65536, need - len(buf)))
                    if not chunk:
                        raise ConnectionResetError
                    buf += chunk
                data, buf = buf[:need], buf[need:]
                up = UPLOAD_PENDING
                UPLOAD_PENDING = None   # 模块级复位（多客户端共用，收完即清）
                resp = save_uploaded_motion(up["name"], data)
                print(f"[cmd] <<< {resp}", flush=True)
                sock.sendall((resp + "\n").encode())
            while b"\n" in buf:
                raw, buf = buf.split(b"\n", 1)
                line = raw.decode("utf-8", "replace").strip("\r").strip()
                if not line:
                    continue
                print(f"[cmd] >>> {line}", flush=True)
                for resp in handle_command(line):
                    print(f"[cmd] <<< {resp}", flush=True)
                    sock.sendall((resp + "\n").encode())
                    if resp.startswith("EVT "):
                        time.sleep(0.01)
    except (OSError, ConnectionResetError):
        pass
    finally:
        UPLOAD_PENDING = None
        try:
            sock.close()
        except OSError:
            pass
        if sock in CMD_CLIENTS:
            CMD_CLIENTS.remove(sock)
        print("[cmd] 客户端断开", flush=True)


def maybe_emit_play_done() -> None:
    """非 loop 动作播完 → EVT PLAY <name> DONE（一次），回到 manual 保持末帧。"""
    evt: str | None = None
    with STATE.lock:
        if STATE.mode != "motion" or STATE.motion is None:
            return
        elapsed_ms = (time.perf_counter() - STATE.motion_t0) * 1000.0
        if (not STATE.motion_loop and not STATE.motion_done_sent
                and elapsed_ms >= STATE.motion["duration_ms"]):
            STATE.motion_done_sent = True
            evt = f"EVT PLAY {STATE.motion_name} DONE"
            end_angles = list(STATE.motion["tracks"]["servo"][-1]["ch"])
            STATE.mode = "manual"
            STATE.manual_angles = [float(a) for a in end_angles]
            STATE.pose = "stand"
            STATE.motion = None
    if evt is not None:
        for sock in list(CMD_CLIENTS):
            try:
                sock.sendall((evt + "\n").encode())
            except OSError:
                pass
        print(f"[sim] 动作播放完成: {evt.split()[-2]}", flush=True)


CMD_CLIENTS: list[socket.socket] = []


# ---------------------------------------------------------------------------
# 物理线程：步进 + 渲染 + 广播

def physics_loop(no_video: bool) -> None:
    model = load_model()
    data = mujoco.MjData(model)
    act_ids = [mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_ACTUATOR, f"{n}_servo")
               for n in JOINT_ORDER]
    base_id = mujoco.mj_name2id(model, mujoco.mjtObj.mjOBJ_BODY, "base")

    renderer = None
    if not no_video:
        renderer = mujoco.Renderer(model, height=VID_H, width=VID_W)
        cam = mujoco.MjvCamera()
        cam.lookat[:] = [0, 0, 0.05]
        cam.distance = 0.35
        cam.azimuth = 135
        cam.elevation = -25

    hub = VIDEO_HUB
    from PIL import Image
    import io

    wall0 = time.perf_counter()
    t0 = data.time
    over_since = 0.0
    next_report = 5.0
    next_frame = wall0
    dt = model.opt.timestep

    print("[sim] 物理线程启动（实时同步）", flush=True)
    while True:
        # 复位请求（摔倒恢复 / RESET / REBOOT）
        if RESET_FLAG_holder:
            RESET_FLAG_holder.clear()
            data = mujoco.MjData(model)
            wall0 = time.perf_counter()
            t0 = 0.0
            over_since = 0.0
            print("[sim] 仿真已复位到站立位", flush=True)

        # 取当前目标角度
        with STATE.lock:
            if STATE.mode == "motion" and STATE.motion is not None:
                elapsed_ms = (time.perf_counter() - STATE.motion_t0) * 1000.0
                dur = STATE.motion["duration_ms"]
                t_ms = elapsed_ms % dur if STATE.motion_loop else min(elapsed_ms, dur)
                angles = sample_motion(STATE.motion, t_ms)
            else:
                angles = list(STATE.manual_angles)

        for act, a in zip(act_ids, angles):
            data.ctrl[act] = math.radians(a)
        mujoco.mj_step(model, data)

        # 摔倒检测（连续 0.3 s 超限才判摔）
        h = float(data.xpos[base_id][2])
        tilt = math.degrees(math.acos(float(np.clip(data.xmat[base_id][8], -1, 1))))
        fallen = h < FALL_HEIGHT or tilt > FALL_TILT_DEG
        over_since = over_since + dt if fallen else 0.0
        if over_since >= FALL_CONFIRM_S:
            print(f"[sim] ⚠ 摔倒（高度 {h:.3f} m，倾角 {tilt:.0f}°），自动复位站立", flush=True)
            RESET_FLAG_holder.append(time.time())
            with STATE.lock:
                STATE.fallen = True
            over_since = 0.0

        # 实时同步：仿真时钟对齐墙钟
        target = wall0 + (data.time - t0)
        ahead = target - time.perf_counter()
        if ahead > 0:
            time.sleep(ahead)

        # 5 Hz 视频帧
        now = time.perf_counter()
        if renderer is not None and now >= next_frame:
            next_frame = now + 1.0 / VID_FPS
            mujoco.mj_forward(model, data)
            renderer.update_scene(data, camera=cam)
            buf = io.BytesIO()
            Image.fromarray(renderer.render()).save(buf, format="JPEG", quality=80)
            hub.broadcast(buf.getvalue())

        if now - wall0 >= next_report:
            next_report += 5.0
            print(f"[sim] t={data.time:6.1f}s  高度={h:.3f}m  倾角={tilt:4.1f}°  "
                  f"姿态={STATE.pose}", flush=True)

        maybe_emit_play_done()


VIDEO_HUB = VideoHub()


def main() -> int:
    global CMD_CLIENTS
    no_video = "--no-video" in sys.argv

    threads = [
        threading.Thread(target=serve_commands, args=(CMD_PORT,), daemon=True),
    ]
    if not no_video:
        threads.append(threading.Thread(target=serve_video, args=(VID_PORT, VIDEO_HUB), daemon=True))
    phys = threading.Thread(target=physics_loop, args=(no_video,), daemon=True)

    for t in threads:
        t.start()
    phys.start()   # 先起物理线程（加载模型要 1-2 s，之后指令线程其实已就绪）

    print(f"[sim] MuJoCo 仿真服务已启动：指令 {CMD_PORT} / 视频 {VID_PORT}"
          f"{'（已禁用）' if no_video else ''}", flush=True)
    print("[sim] 等待上位机连接…（Ctrl+C 退出）", flush=True)
    try:
        while True:
            time.sleep(1)
    except KeyboardInterrupt:
        print("[sim] 退出", flush=True)
        VIDEO_HUB.close_all()
    return 0


if __name__ == "__main__":
    sys.exit(main())
