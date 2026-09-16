# firmware/ — ESP32 固件

> 这里放嵌入式同学的代码。详细技术选型见 [`docs/01-分角色技术方案.md`](../docs/01-分角色技术方案.md) 第二部分。

## 技术栈（全部现成库）

| 用途 | 库 |
|---|---|
| 框架 | ESP32 Arduino Core |
| 舵机驱动 | Adafruit_PWMServoDriver（PCA9685） |
| Web 服务 | ESPAsyncWebServer |
| WebSocket | ArduinoWebSockets |
| 显示 | Adafruit_SSD1306 + GFX |
| JSON | ArduinoJson |
| 文件系统 | LittleFS |
| OTA | ArduinoOTA |
| 调度 | FreeRTOS（ESP32 自带） |

## 建议引脚分配

| 功能 | 引脚 |
|---|---|
| I2C SDA / SCL | GPIO 8 / 9（接 PCA9685 + OLED + 扩展口） |
| 电池电压检测 | ADC |
| 悬崖检测 ×2 | ADC 或 GPIO |
| 状态 LED | GPIO |

**8 路舵机全部走 PCA9685，不占用 MCU 的 PWM**（PCA9685 还剩 8 路给扩展舵机）。

## 必须记住的三条

1. **舵机必须 180° 版本**（360° 版无法定位）
2. 电源轨并 **1000 µF 电容**，软件限制同时动作的舵机数量
3. 固件要提供**单舵机中位标定**（SG90 批次差异 5–10°）

## 与软件的接口

软件同学负责定义、你负责实现：`.motion` 格式、HTTP 命令表、WebSocket 消息表。
