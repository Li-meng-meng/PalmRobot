# web/ — 网页前端

所有面向用户的东西都是网页，**不做 App 打包**。

## 三个页面

| 页面 | 干什么 | 跑在哪 |
|---|---|---|
| 控制台 | 遥控、动作触发、表情切换、电量 | 内嵌在 ESP32 固件里，打开 `192.168.4.1` |
| 3D 预览 | 在浏览器里看机器人动 | 本地 / 局域网 |
| 动作编辑器 | 表格编排关键帧，导出 `.motion` | 本地 / 局域网 |

## 技术栈

| 用途 | 用什么 |
|---|---|
| 3D 预览 | **three.js**（不需要物理引擎，纯运动学） |
| 表情识别 | **MediaPipe Face Landmarker (Web)** — 原生输出 52 blendshape |
| 姿态识别 | **MediaPipe Pose Landmarker (Web)** |
| 音乐节拍 | **Web Audio API** |
| 通信 | WebSocket + fetch |

## 关键约束

| 约束 | 说明 |
|---|---|
| 摄像头需要 HTTPS 或 localhost | 浏览器安全策略，`file://` 打不开摄像头 |
| 由 ESP32 托管网页 | 同源，顺手解决 CORS |
| 手机优先 | 至少覆盖安卓 Chrome，iOS 后期适配 |
| 锁定 three.js 版本 | 它的 API 变化较快 |
