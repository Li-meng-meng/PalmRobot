# ai/ — 视频处理（视频→动作）

> 方案：[`docs/11-软件方案.md`](../../docs/11-软件方案.md) §6（视频处理三级路径 L1→L2→L3）

**状态：L1 律动级 + L2 精华级均已打通并仿真验证**（P2 提前；L3 按方案放弃）。

## L2 精华级 — 人体姿态重定向（pose_to_motion.py）

从视频里提取**人体动作**并重定向到 8 自由度：MediaPipe PoseLandmarker
逐帧 33 关键点 → 低维特征 → 8DOF 映射 → 平滑/限幅 → `.motion`（非循环）。

```powershell
# 在 software/ 目录下
simulation\.venv\Scripts\python ai\pose_to_motion.py 视频.mp4 输出.motion
simulation\.venv\Scripts\python ai\pose_to_motion.py 视频.mp4 输出.motion --export 关键点.json
```

**重定向映射**（8 自由度只能表达这些，"精华"级的含义）：

| 人体特征 | 机器人动作 |
|---|---|
| 髋中点 x 左右偏移 | R1/L1 反向对摆（重心跟着画面左右移） |
| 髋中点 y 上下蹲起 | 四膝同步蹲起 |
| 手腕高于同侧肩 | 该侧前腿抬起（wave 原型） |

- 参考位置（ref_x/ref_y）取全程中位数**自校准**，人不必站画面正中
- EMA 平滑抑抖；关键帧间隔 100ms 起自适应收紧，保证 ≤16KB 规范上限
- 模型：`models/pose_landmarker_lite.task`（5.5MB，MediaPipe 官方下载）
- **中文路径坑**：mediapipe/cv2 的 C 层与 MuJoCo 一样打不开中文路径，
  脚本已自动把模型/视频复制到 ASCII 临时路径中转

**调试通道**：`--landmarks 关键点.json` 可跳过 mediapipe 直接注入关键点
（与 `--export` 同格式，可互换）。`ai/test/make_landmarks.py` 生成合成关键点序列。

## L1 律动级 — 节奏驱动（video_to_motion.py）

把一段视频/音乐的**节奏**变成机器人律动：ffmpeg 抽音频 + numpy 分析。

```powershell
simulation\.venv\Scripts\python ai\video_to_motion.py 视频.mp4 输出.motion
# 可选：--bpm 150 指定节拍 / --beats 16 循环拍数 / --no-validate 跳过二次校验
```

| 步骤 | 做法 |
|---|---|
| 抽音频 | imageio-ffmpeg 静态 ffmpeg → 单声道 16k PCM（PCM 落 ASCII 临时路径） |
| 估 BPM | onset（RMS 一阶差正半波）归一化自相关，在 60–180 BPM 滞后窗内取峰 |
| 能量分档 | 每拍平均能量 vs 全局 33%/66% 分位数 → low / medium / high |
| 动作原型 | 8 拍循环：每半拍 交替[摇摆帧 / 回站立]，R1/L1 反向摆 + 前膝回弹 |
| 档位→幅度 | low 20°/0°，medium 28°/8°，high 35°/12°（髋摆半幅/前膝回弹） |

**兜底原则（docs/11 §6）**：无音频流 / 静音 / 无清晰节拍 → BPM 100 中档。
任何输入必有输出。

## 映射表

[`rhythm_map.json`](rhythm_map.json) — L1 参数（BPM 限幅、能量分位数、档位幅度、通道号、站立位），
**Python 与 JS 共读同一份**（docs/11 §6 约定）。L2 参数直接写在 pose_to_motion.py 头部常量。

## 看动作的三种方式

```powershell
simulation\.venv\Scripts\python tools\motion_gen.py validate <out.motion>       # 格式校验
simulation\.venv\Scripts\python simulation\motion_player.py <out.motion> --json 报告.json   # 仿真验证不摔
simulation\.venv\Scripts\python simulation\motion_player.py <out.motion> --record 动作.mp4  # 离屏渲染录像
```

| 方式 | 命令/操作 | 说明 |
|---|---|---|
| **MP4 录像** | `--record out.mp4` | 离屏渲染整段动作，双击播放，最稳 |
| **浏览器 3D** | 双击 `web/index.html` → "打开 .motion 文件" 或拖入视口 | 交互预览（three.js 运动学） |
| 交互 viewer | `--view` | GLFW 窗口，**本机环境实测无法显示**（窗口创建后立即关闭），留作其它机器用 |

上位机（desktop）"3D 预览"标签页同样内嵌 web 组件，连接模拟机器人后可同步调试。

## 验证结果（2026-09-18，MuJoCo 3.13）

| 输入 | 结果 |
|---|---|
| L1：合成 120BPM 节拍视频 | BPM 精确检出 120.0，全 high 档，仿真 **PASS** 倾角 0.8° |
| L1：无音频流视频 | 兜底 100 BPM，仿真 **PASS** 倾角 0.5° |
| L1：`--bpm 150` 覆盖 | 仿真 **PASS** 倾角 0.7° |
| L2：合成关键点 300 帧（移步→蹲起→举手） | 30.4s 动作，仿真 **PASS** 倾角 1.9° |
| L2：无人视频 | 正确报"全程未检测到人体" |
| L2：真实视频 `test/bboy_steve.mp4`（Wikimedia Commons 街舞素材，1080p 15s） | 生成 ~11KB/15.3s（50ms 密采样），仿真 **PASS** 倾角 12.9° |
| L2：真实视频 `test/hand_gestures.mp4`（Wikimedia Commons Bharatanatyam 手位教学，720p 15s） | 生成 8.6KB/15.3s（连续特征+真实时间轴），仿真 **PASS** 倾角 0.2° |

## 关节级编排动作库（tools/make_dances.py）

不经过视频重定向、直接按 8 关节结构编排的成套舞蹈（`tools/motions/`，均 loop 循环，
默认经 `motion_gen.beautify` 缓动烘焙后落盘）：

| 动作 | PLAY 名 | 时长 | 设计 | 仿真 |
|---|---|---|---|---|
| 太极 | `tai_chi` | 12s | 起势蹲起（渐强两蹲两起）→ 云手（侧倾+俯仰 90° 相位差画椭圆）→ 揽雀尾前倾后坐 → 收势；脊柱波贯穿 | **PASS** 倾角 0.4° |
| 天鹅舞 | `swan` | 4.8s | 踮脚起伏 → 展翅（缓开急合非对称包络 + 挺胸）→ 对角碎步 → 天鹅回首（smoothstep 沉入） | **PASS** 倾角 1.5° |
| 蹦恰恰 | `cha_cha` | 3.2s | 快快慢节奏 × 2，重拍顶胯下压 + 髋部画小圆（s/l 相位差 90°） | **PASS** 倾角 1.6° |
| 华尔兹 | `waltz` | 6s | 3/4 拍 120BPM：强拍沉弱拍升（rise and fall）+ 重心绕椭圆 + 前后膝错位起伏 | **PASS** 倾角 2.8° |
| 月光流 | `moonflow` | 8s | 冥想向：侧倾与俯仰合成 Lissajous 8 字，一笔画不间断 | **PASS** 倾角 1.5° |
| 猫式伸展 | `yoga` | 6.4s | 对角腿对轮流压低—保持（呼吸颤）—收回，重心沉向被压对角 | **PASS** 倾角 2.1° |
| 待机呼吸 | `idle` | 3.2s | CPG：膝 2~4.5° 慢起伏（前膝先吸后膝 0.3s 跟上）+ 重心极缓游移，"永远不完全静止" | **PASS** 倾角 0.1° |
| 蹦跳 | `bounce` | 1.6s | CPG：150BPM，预备下蹲 → 蹬伸 → 二次回弹（动画十二原则） | **PASS** 倾角 1.0° |

> **可视幅度**（`simulation/motion_probe.py` 实测身体水平漂移）：cha_cha 27.2mm >
> moonflow 18.9mm > waltz 15.2mm ≈ swan 14.9mm ≈ yoga 15.8mm > tai_chi 10.3mm >
> bounce 6.5mm > idle 0.5mm（待机本就该似有似无）。"摔不摔"之外，位移太小 =
> 观众看不出在动，调编排时两个数一起看。

另有 `oc_*.motion` 25 个——Petoi OpenCat 动作库经 `tools/opencat_to_motion.py` 转换
（姿态 11 + 步态 14，保手势映射，仿真 25/25 PASS），同目录可用 `PLAY oc_vtf` 等播放。

上位机"预设姿态"下方有对应按钮（PLAY 命令），3D 预览同步播放；渲染录像在 `ai/test/*_sim.mp4`。

## 三级路径位置

| 级 | 状态 | 说明 |
|---|---|---|
| L1 律动级 | ✅ 本目录 | 节奏→参数化律动，任何视频可用 |
| **L2 精华级** | ✅ 本目录 | MediaPipe Pose 人体动作重定向到 8 自由度 |
| L3 风格迁移 | 放弃 | 8 自由度表达不了 |
