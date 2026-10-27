# web/ — 3D 预览组件

> **这不是产品形态。**
> 原计划的"用户网页控制台"已取消（改为原生上位机）。本目录只剩一个用途：
> **被上位机用 WebView2 内嵌，做动作的 3D 预览。**

**状态：P0 已完成并验证**（浏览器 + 上位机 3D 预览标签页均可跑，浏览器验证 4/4 通过）。

---

## 快速开始

```powershell
# 方式一：双击 index.html（file:// 直接可用，资产已内嵌 base64）
# 方式二：上位机内嵌 —— "3D 预览"标签页自动加载本页
# 方式三：本地服务
python -m http.server 8123   # 打开 http://127.0.0.1:8123/index.html
```

- 右侧 8 舵机滑块（0–180°，带机械限位）→ 模型实时动
- 按钮播放内嵌动作包：`walk` / `wave` / `dance` / `stand`

## 文件

| 文件 | 说明 |
|---|---|
| `index.html` | 演示页 + WebView2 加载页（HUD 深色风格） |
| `preview.js` | 核心组件：MJCF 解析 → three.js 层级 → 运动学驱动 |
| `build_assets.py` | 把 MJCF + 11 个 STL + 样例 motion 打包成 `assets/robot-assets.js`（base64 内嵌，使 file:// 免服务可用）。**assets 更新后重跑** |
| `assets/robot-assets.js` | 生成物（~3.3 MB），勿手改 |
| `vendor/` | three.js r147（UMD）+ OrbitControls + STLLoader，**版本已锁定** |

## 实现要点（重要）

| 点 | 说明 |
|---|---|
| 模型来源 | **直接解析 `simulation/assets/mjcf/sesame.xml`**（DOMParser），不用 urdf-loader——MJCF 里 body/joint/quat/geom 与 MuJoCo 语义完全一致 |
| 关节映射 | `旋转角 = radians(舵机角) − MJCF joint@ref`，hinge axis `0 0 -1`；`ref` 即站立位角（135/45/45/135/0/180/0/180），`ctrl` 域 == 固件 0–180° 域 |
| 渲染范围 | 只渲染 `class="visual"` 几何；collision/foot/site 忽略 |
| 坐标系 | MuJoCo Z-up，`camera.up = (0,0,1)` |
| .motion 播放 | servo 轨道线性插值（固件同款语义），`loop` 循环 |

## 公开 API（挂 `window.PalmRobotPreview`）

| 方法 | 说明 |
|---|---|
| `init(container)` | 初始化渲染（Promise） |
| `setServoAngles([8])` | 固件通道角，ch 序 R1,R2,L1,L2,R4,R3,L3,L4 |
| `playMotion(nameOrJson)` | 内嵌名（walk/wave/dance/stand）或 .motion JSON |
| `stopMotion()` / `resetPose()` | 停止 / 回站立位 |

**WebView2 通信**：宿主发 `{type:'angles', value:[...]}` / `{type:'motion', name:'...'}`；
页面回 `{type:'preview-ready'}` 与状态消息。上位机已实现：连接后拖滑块 / POSE 按钮同步驱动 3D。
（wave/dance 等固件播放动作暂不同步 3D，P1 动作编辑器接入。）

---

## 用途

| 用途 | 说明 |
|---|---|
| 动作编辑器预览 | 调关键帧时实时看到机器人姿态 |
| 步态检查 | 把仿真输出的角度序列可视化 |
| 演示素材 | 录屏做演示视频 |

**谁用它**：`software/desktop/` 的 WPF 上位机。

---

## 技术栈

| 用途 | 用什么 |
|---|---|
| 3D 渲染 | **three.js r147**（UMD，锁定版本） |
| 模型加载 | `STLLoader`（sesame STL，scale 0.001 mm→m） |
| 运动学 | 解析 MJCF 层级 + 关节旋转（免物理引擎） |
| 与上位机通信 | WebView2 JS Interop（WebMessage） |

**不需要物理引擎** —— 预览只验证"角度序列看起来对不对"，这是运动学问题。

---

## 模型从哪来

| 资产 | 来源 |
|---|---|
| MJCF / STL / 标定 | 复用 `simulation/assets/`（lukehollis/sesame-ml），**单一数据源** |
| 更新方式 | 改 `simulation/assets/` 后重跑 `python build_assets.py` |

---

## 关键约束

| 约定 | 说明 |
|---|---|
| 锁定 three.js 版本 | r147 UMD，vendor/ 内置，无网络依赖 |
| file:// 可用 | 资产 base64 内嵌（浏览器禁 file:// fetch） |
| 不做成网页产品 | 不要再加遥控、设置之类的功能 |
