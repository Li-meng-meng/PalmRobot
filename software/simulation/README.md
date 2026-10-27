# simulation/ — 步态仿真

> 上位机软件的一部分。详见 [`docs/11-软件方案.md`](../../docs/11-软件方案.md) 第 5 节

**目标**：在烧硬件之前，先在仿真里把步态调稳。

---

## 为什么必须做仿真

> 四足最大的风险是"**角度序列看着对，实际上一走就摔**"。
> MuJoCo 有真实的重力、接触和力矩，**能在你烧硬件之前就把这个问题算出来**。
> 团队的启动资金只有 ¥300，一次失败就可能要停一个月 —— 仿真是最便宜的保险。

---

## 模型是现成的，不要自己建

| 资产 | 来源 | 用途 |
|---|---|---|
| **MJCF** | `lukehollis/sesame-ml` → `assets/mjcf/sesame.xml` | 直接给 MuJoCo 用 |
| **URDF** | 同上 → `assets/urdf/sesame.urdf` | three.js / ROS 通用 |
| 标定数据 | 同上 → `assets/calibration/default.yaml` | 关节零位参数 |
| 完整 CAD | `dorianborian/sesame-robot` → `hardware/cad/*.step` | 改结构时用 |

```bash
git clone https://github.com/lukehollis/sesame-ml.git
# 把 assets/ 拷到本目录下使用
```

> MJCF 是 CAD 派生的，自带质量、惯量、执行器限位和接触几何，**仿真结果有物理意义**。
> **P0 的 3D 建模工作量 = 0。**

---

## 与环境准备

```bash
python -m venv .venv
.venv\Scripts\activate
pip install mujoco numpy
```

---

## 任务

| # | 任务 | 产出 | 状态 |
|---|---|---|---|
| T2 | 环境搭建 + 加载模型 | `sim_load.py`（加载 + 站立 2 s 稳定验证 + 离屏渲染） | ✅ 站立稳定（倾角 0.02°） |
| T3 | 站立 → 原地踏步 → 直线行走 | `gait_sim.py` + **`gait_params.json`** + **`walk.motion`** | ✅ 3 s 前进 0.122 m 不摔 |
| T4 | **仿真服务（上位机控制虚拟引擎）** | `sim_server.py` + `motion_player.py` | ✅ 上位机"仿真机器人"直连，dance 循环 10 s 不摔 |
| T5 | 动作可视幅度探针 | `motion_probe.py`（高度区间 / 水平漂移 / 倾角区间） | ✅ 配合摔跤判定用：位移太小 = 观众看不出在动 |

### sim_server.py — 仿真服务（上位机接入）

```powershell
.venv\Scripts\python sim_server.py      # Ctrl+C 退出（上位机连"仿真机器人"也会自动拉起）
```

| 端口 | 用途 |
|---|---|
| `tcp://127.0.0.1:8555` | 指令通道，docs/20 行协议（READY 横幅 / PING / VER / STATUS / POSE / STOP / MOVE / FRAME / PLAY / LISTM / RESET / EVT PLAY） |
| `tcp://127.0.0.1:8556` | 视频流，5 Hz JPEG，每帧 4 字节大端长度前缀 + JPEG（320×240） |

- POSE wave/dance 与 `PLAY <name>` 播放 `tools/motions/*.motion`（固件同款插值），非循环播完发 `EVT PLAY <name> DONE`
- `MOVE v w` 循环播放 walk.motion，`STOP` 回站立；摔倒自动复位站立（控制台有 ⚠ 日志）

**T3 的产出直接交给嵌入式同学烧进板子**，这是 P0 最核心的交付。

### 当前步态参数（v0.1，仿真验证）

| 参数 | 值 | 说明 |
|---|---|---|
| `freq` | 3.3 Hz | 步频 |
| `hip_amp` | 28° | 髋摆幅 |
| `hip_mode` | `same` | 对角组内两髋同号摆动 |
| `hip_sign` | +1 | 前进方向 |
| `knee_f/r` | 50° | 膝抬起幅度 |
| `knee_lead_deg` | -45° | 膝相位提前（提前抬腿离地） |
| 速度 | ≈4 cm/s | 3 s 0.122 m，最大倾角 9.6° |

复跑：`.venv\Scripts\python gait_sim.py`（完整搜索）/ `--quick`（默认参数验证）。

---

## 已踩的坑（重要）

| 坑 | 说明 |
|---|---|
| **中文路径** | MuJoCo 的 C 层在 Windows 打不开含中文的路径，统一走 `from_xml_string` + assets 字典加载（见 `sim_load.load_model`） |
| **mesh 目录分离** | MJCF 与 STL 分属 `mjcf/`、`meshes/` 且 XML 未写 `meshdir`，必须用 assets 字典注入 |
| **ctrl 越界被钳制** | MJCF 执行器 `ctrlrange` 会静默截断越界 ctrl，仿真"能跑"不代表角度合法——导出 `.motion` 后必须用 `tools/motion_gen.py validate` 校验（已抓到 215°/-35° 越界案例） |
| 膝基准方向 | R3/L4 站立 180°（折叠=减小），L3/R4 站立 0°（抬起=增大），写反会"看起来在动但不前进" |

---

## 与预览组件的分工

| 工具 | 回答什么问题 | 用在哪 |
|---|---|---|
| **MuJoCo**（这里） | "这样走**会不会摔**？" | 设计步态 |
| three.js（`../web/`） | "动作**看起来**对不对？" | 展示结果 |

**两者输入同一份 8 通道角度序列，不冲突。**

---

## 坑

| 坑 | 说明 |
|---|---|
| 改了外壳后质量变化 | 仿真与实物会有偏差，需**用实物称重回填质量 / 惯量参数** |
| 不要过度追求物理精确 | 先让它在仿真里走稳，再谈优化 |
| 步态调参容易超期 | 先只做站立与直线，转向延后 |
