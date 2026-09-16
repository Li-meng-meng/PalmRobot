# software/ — 软件工程

## 环境准备

| 用途 | 需要装 |
|---|---|
| 步态仿真 | Python 3.10+、MuJoCo |
| 网页前端 | 任意现代浏览器（Chrome 优先） |
| AI 功能 | 浏览器即可（MediaPipe Web），无需 Python |
| 工具脚本 | Python 3.10+ |

```bash
python -m venv .venv
.venv\Scripts\activate        # Windows
pip install mujoco numpy
```

## 目录分工

| 目录 | 放什么 | 语言 |
|---|---|---|
| `simulation/` | MuJoCo 步态仿真、参数调优、仿真视频 | Python |
| `web/` | 网页控制台、动作编辑器、3D 预览 | HTML / JS |
| `tools/` | 动作包生成、出厂标定、订单脚本 | Python |
| `ai/` | 视频动作提取、表情识别、音乐节拍 | Python（原型）+ JS（落地） |

## 立刻要做的三件事

> 来自《软件工程师任务规划》P0，按这个顺序：

1. **T1.3 定义 `.motion` 动作格式**（2 天）—— 它阻塞嵌入式同学，**优先做**
2. **T1.1 MuJoCo 环境 + 加载 sesame 模型**（2–3 天）—— 模型是现成的，见 `simulation/README.md`
3. **T1.2 步态仿真与调参**（8–10 天）—— P0 最耗时的核心任务

## 重要约定

| 约定 | 原因 |
|---|---|
| **映射表存成 JSON，Python 与 JS 读同一份** | 否则两边逻辑必然漂移 |
| 动作文件格式冻结后只加字段、不改语义 | 避免嵌入式返工 |
| 网页优先，不做 App 打包 | 省成本，手机浏览器够用 |
| 仿真模型直接用 sesame 现成的 | 不要自己建模 |

## 不在这里做的事

| 内容 | 归属 |
|---|---|
| 固件、舵机驱动、网络服务端 | `firmware/` |
| 结构件、PCB、BOM | `hardware/` |
