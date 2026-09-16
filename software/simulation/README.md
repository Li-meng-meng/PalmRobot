# simulation/ — 步态仿真

**目标**：在烧硬件之前，先在仿真里把步态调稳。

## 模型是现成的，不要自己建

| 资产 | 来源 | 用途 |
|---|---|---|
| **MJCF 模型** | `lukehollis/sesame-ml` → `assets/mjcf/sesame.xml` | 直接给 MuJoCo 用 |
| **URDF 模型** | `lukehollis/sesame-ml` → `assets/urdf/sesame.urdf` | three.js / Unity / ROS 通用 |
| 标定数据 | `lukehollis/sesame-ml` → `assets/calibration/default.yaml` | 关节零位参数 |
| 完整 CAD | `dorianborian/sesame-robot` → `hardware/cad/*.step` | 要改结构时用 |

```bash
git clone https://github.com/lukehollis/sesame-ml.git
# 把 assets/ 拷到本目录下使用
```

> MJCF 是 CAD 派生的，自带质量、惯量、执行器限位和接触几何，**仿真结果有物理意义**。

## 任务顺序

| # | 任务 | 产出 | 工时 |
|---|---|---|---|
| T1.1 | 环境搭建 + 加载模型 | 能在 viewer 里看到机器人 | 2–3 天 |
| T1.2 | 站立 → 原地踏步 → 直线行走 | 步态参数表（JSON）+ 仿真视频 | 8–10 天 |

## 坑

| 坑 | 说明 |
|---|---|
| 改了外壳后质量变化 | 仿真与实物会有偏差，需**用实物称重回填参数** |
| 不要过度追求物理精确 | 先让它在仿真里走稳，再谈优化 |
