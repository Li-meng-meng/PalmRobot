# software/ — 上位机（软件主线）

> 详细方案见 [`docs/11-软件方案.md`](../docs/11-软件方案.md)

**上位机是"大脑"**：所有需要算力的工作都在这里，通过小文本指令驱动机器人。

| 上位机负责 | 机器人负责 |
|---|---|
| 处理视频（→ 动作精华）、处理图片（表情 / 手势） | 播放动作、渲染表情 |
| 基本设置、参数配置 | 接收配置 |
| 仿真、生产工具（标定 / 烧录 / 测试） | 被操作 |

**机器人只收小文本指令（几十字节到几 KB），永不接收视频或图片。**

---

## 目录分工

| 目录 | 放什么 | 语言 | 优先级 |
|---|---|---|---|
| **`desktop/`** | **WPF 上位机（主线）** | C# / .NET 8 | P0 起 |
| `simulation/` | MuJoCo 步态仿真与调参 | Python | P0 起 |
| `ai/` | 视频处理、图片处理（表情 / 手势） | Python 原型 + 上位机落地 | P2 起 |
| `tools/` | 量产与运营脚本 | Python | P1 起 |
| `web/` | **3D 预览组件**（供上位机 WebView2 内嵌） | HTML / JS | P0 起 |

> **注意**：`web/` **不是产品形态**，只是一段被上位机内嵌的预览组件。原计划的"用户网页控制台"已取消。

---

## 语言分工

| 语言 | 用在哪 |
|---|---|
| **C# / WPF** | 电脑端上位机（主线） |
| **C# / MAUI** | App（V3 起），复用 `PalmRobot.Core` |
| **Python** | 仿真、算法原型、量产脚本 |
| JavaScript | 仅 3D 预览组件 |

> **C# 管交付，Python 管把东西做对，JS 只在预览组件里出现。**

---

## 上位机快速开始（desktop/）

```powershell
cd desktop
dotnet build                 # 或直接开 PalmRobot.sln
dotnet run --project src/PalmRobot.App
dotnet test                  # 53 个单元测试（协议 / 校验 / 客户端配对）
```

- 无硬件时选左侧 **"模拟机器人"**（内置固件仿真器，完整应答 STATUS / FRAME / PUTM 等指令）
  或 **"仿真机器人"**（MuJoCo 物理仿真：指令走 docs/20 行协议、姿态驱动真实物理、"仿真画面"标签页回传 5 Hz 渲染画面；服务未运行会自动拉起 `simulation/sim_server.py`）
- 真机插 USB 后点"扫描串口"，115200 8N1
- 调试页：8 舵机滑块（带机械限位）拖动即发 `FRAME`（5Hz 节流）、预设姿态 / 表情、急停；挥手/跳舞等动作播放与"3D 预览"页联动
- "视频转动作"页：导入视频 → L1 律动 / L2 姿态重定向生成 `.motion` → 自动 3D 预览 → **"上传到设备并播放"**（仿真机器人 = 虚拟引擎，真机 = USB 串口，同一套 PUTM + PLAY 流程）
- 命令行页：敲原始指令（与串口一致），日志页：全部收发原始行

---

## 环境准备

| 用途 | 需要装 |
|---|---|
| 上位机开发 | .NET 8 SDK + Visual Studio 2022（含 WPF 工作负载） |
| 仿真 | Python 3.10+ + MuJoCo |
| 预览组件 | 任意现代浏览器（Chrome 优先） |

```powershell
dotnet --version        # 确认 .NET 8
python -m venv .venv
.venv\Scripts\activate
pip install mujoco numpy
```

---

## 立刻要做的三件事（P0）

| 顺序 | 任务 | 状态 | 为什么这个顺序 |
|---|---|---|---|
| 1 | **定指令集 + `.motion` 格式**（3 天） | ✅ 完成：`docs/20`、`docs/21` + `tools/motion_gen.py` + 4 个样例动作包 | **它阻塞嵌入式同学**，必须第 1 周完成 |
| 2 | **MuJoCo 加载 sesame 模型并调步态**（11 天） | ✅ 完成：`simulation/`（加载验证 + 步态网格搜索 + `gait_params.json` + `walk.motion`） | P0 最核心，仿真里调稳实机才走得稳 |
| 3 | **上位机骨架 + 设备管理 + 调试面板**（7 天） | ✅ 完成：`desktop/PalmRobot.sln`（53 个单元测试全过；内置固件仿真器，无硬件可玩） | 让上位机先变得有用 |
| 4 | 3D 预览组件（three.js 内嵌） | ✅ 完成：`web/`（解析 MJCF 建模 + .motion 播放 + 上位机"3D 预览"标签页 WebView2 同步） | 动作编辑器的可视化依赖 |
| 5 | 视频→动作 L1 律动级 | ✅ 完成：`ai/video_to_motion.py`（音频→BPM/能量→律动 .motion，仿真验证不摔，见 `ai/README.md`） | P2 提前，打通"视频→动作→仿真"全链路 |
| 6 | 视频→动作 L2 精华级 | ✅ 完成：`ai/pose_to_motion.py`（MediaPipe 人体姿态→8DOF 重定向，仿真验证不摔） | P2 提前 |
| 7 | **上位机控制虚拟引擎 + 视频转动作一键链路** | ✅ 完成：`simulation/sim_server.py`（8555 行协议含 PUTM 上传 + 8556 视频流）+ `PalmRobot.Core` TcpTransport + 上位机"仿真机器人"设备与"仿真画面"标签页 + "视频转动作"标签页（导入视频 → L1/L2 生成 → 自动 3D 预览 → PUTM 下发） | 滑块/动作指令驱动真实物理，画面实时回传；视频产物一键进虚拟引擎或真机 |
| 8 | **动作美化与生命感（P1）** | ✅ 完成：`tools/motion_gen.py beautify`（缓动烘焙：Catmull-Rom + 自适应采样，"好看"做在生成端、固件保持线性）+ `tools/make_dances.py` CPG 生命感动作（idle 呼吸 / bounce 蹦跳）+ `tools/opencat_to_motion.py` OpenCat 动作库转换器（25 个 `oc_*.motion`，仿真 25/25 PASS，见 `docs/21` §6） | 无硬件阶段先把动作质量拉满；开源社区经验（Petoi OpenCat / 动画十二原则）直接吸收 |
| 9 | **互动情绪动作包 + 膝折叠方向勘误（P1 内容）** | ✅ 完成：`make_dances.py` 新增 yes 点头 / no 摇头 / happy 开心 / act_cute 撒娇 / startled 惊吓 / bow 鞠躬 / greet_show 开机欢迎（带 face 轨道，桌面调试页同步加按钮）；MuJoCo 实测发现并修正左膝折叠符号 bug（原写法左膝恒在限位端点不动），存量 `oc_*.motion` 已按镜像关系迁移，**全库 42/42 仿真 PASS**；顺带修 `motion_player.py --png` 输出真 PNG（原为裸 RGB 字节） | 互动/情绪是掌上宠物的核心内容面；物理引擎实测是校准关节方向语义的唯一可信来源 |
| 10 | **动作优美度升级（P1 内容）** | ✅ 完成：`pose()` 词汇扩展（`kf/kr` 前后膝分离 = 脊柱波、`d` 对角折叠）；重编 `tai_chi`（渐强起势 + 云手画椭圆）/ `swan`（缓开急合 + 对角碎步）/ `cha_cha`（顶胯画圆）/ `idle`（呼吸传导）；新增 `waltz` 华尔兹 / `moonflow` 月光流（Lissajous 8 字）/ `yoga` 猫式伸展（对角拉伸），桌面调试页同步加按钮；新增 `simulation/motion_probe.py` 可视幅度探针；**全库 46/46 仿真 PASS**，最大倾角 2.8° | "优美"可工程化：脊柱波消四腿齐动的机械感、Lissajous 把直线往返变连续的圆、非对称包络消机器感；但幅度必须过探针（位移太小观众看不见）+ 过仿真（大了会摔）双门槛 |

---

## 重要约定

| 约定 | 原因 |
|---|---|
| **领域逻辑抽成 `PalmRobot.Core` 类库** | WPF 现在用，未来 MAUI 直接复用，不用重写 |
| **映射表存成 JSON，Python 与 JS 读同一份** | 否则两边逻辑必然漂移 |
| 指令集冻结后只加字段、不改语义 | 避免嵌入式返工 |
| 仿真模型直接用 sesame 现成的 | 不要自己建模 |
| 机器人只收小文本 | 视频图片永不下发 |

---

## 不在这里做的事

| 内容 | 归属 |
|---|---|
| 固件、舵机驱动、指令服务 | `firmware/` |
| 结构件、PCB、BOM | `hardware/` |
| 外壳造型 | `hardware/` + 设计同学 |
