# desktop/ — WPF 上位机

> 详细方案见 [`docs/04-上位机技术方案.md`](../../docs/04-上位机技术方案.md)

**给内部用**：工程师调试、产线装配标定、批量测试、固件烧录。
**给用户的是网页**（`software/web/`），两者分工明确，不要重复造轮子。

## 建议的解决方案结构

```
desktop/
├── PalmRobot.sln
├── src/
│   ├── PalmRobot.Core/          ← .NET 类库（关键！为 MAUI 铺路）
│   │   ├── Protocol/            通信协议（串口文本 + WebSocket JSON）
│   │   ├── Models/              .motion 数据模型
│   │   ├── Calibration/         标定算法
│   │   └── Validation/          动作包校验
│   ├── PalmRobot.App/           ← WPF 主程序（MVVM）
│   │   ├── Views/               XAML 界面
│   │   ├── ViewModels/
│   │   └── Services/            串口、WebSocket、mDNS
│   └── PalmRobot.Cli/           ← 可选：命令行工具，复用 Core
└── tests/
    └── PalmRobot.Core.Tests/
```

> **`PalmRobot.Core` 是关键。** 协议、数据模型、标定算法都放这里，未来 MAUI App 直接引用，不用重写。

## 技术栈

| 用途 | 库 |
|---|---|
| UI | WPF（.NET 8） |
| MVVM | CommunityToolkit.Mvvm |
| 串口 | System.IO.Ports |
| 局域网 | Makaretu.Dns（mDNS）+ ClientWebSocket |
| 3D 预览 | WebView2（**加载 `../web/` 的页面，复用同一份 three.js 代码**） |
| 图表 | LiveCharts2 或 OxyPlot |
| 日志 | Serilog |
| 打包 | 单文件发布 + Inno Setup |

## 第一个里程碑：设备管理 + 调试面板

| 功能 | 验收 |
|---|---|
| 扫描串口与局域网设备 | 插上 USB 能自动识别到 |
| 连接并显示在线状态 | 状态栏显示固件版本、电压、温度 |
| 单舵机微调 | 拖动滑块，机器人对应舵机跟着动 |
| 快捷动作触发 | 点按钮能播放预设动作 |

**先做这个**，它能立刻让上位机变得有用（否则就只能干等标定需求）。

## 与固件的两条通道

| 通道 | 用在 | 格式 |
|---|---|---|
| 串口（USB） | 标定、烧录、底层调试 | 文本命令，便于人工敲 |
| 局域网 WebSocket | 调试、测试、OTA | JSON，**与网页端同一套命令表** |

> **命令表只有一份**（由软件同学定义），网页和上位机都实现它，固件实现一次。
