#!/usr/bin/env python3
"""split_step.py — 把整机 STEP 汇编拆成单件打印用 .stp。

上游 dorianborian/sesame-robot 的 `Sesame-ESP32-v122.step` 是含电子件
（SG90 舵机 / 主板 / 电池…）的整机 B-rep 汇编；切片和打印只需要打印件。
本工具按装配体里的**零件名**拆分（R/L 是镜像件，命名是唯一可信来源，
包围盒大小只用来核对），导出到 printing/stp/ 供切片器直接导入。

用法：
  python split_step.py list                     # 列出汇编里全部零件（名 + 实体数 + 尺寸）
  python split_step.py split                    # 拆分 PRINT_PARTS 到 ../printing/stp/
  python split_step.py split --parts A B ...    # 指定零件名
  python split_step.py export <名> <out.stp>    # 导出单个零件
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

from OCP.Bnd import Bnd_Box
from OCP.BRepBndLib import BRepBndLib
from OCP.BRepGProp import BRepGProp
from OCP.GProp import GProp_GProps
from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.STEPControl import STEPControl_StepModelType
from OCP.STEPControl import STEPControl_Writer
from OCP.STEPCAFControl import STEPCAFControl_Reader
from OCP.TCollection import TCollection_ExtendedString, TCollection_AsciiString
from OCP.TDataStd import TDataStd_Name
from OCP.TDF import TDF_Label
from OCP.TDocStd import TDocStd_Document
from OCP.TopAbs import TopAbs_SOLID
from OCP.TopExp import TopExp_Explorer
from OCP.TopoDS import TopoDS_Shape
from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ShapeTool
from OCP.collections import Sequence_TDF_Label

HERE = Path(__file__).resolve().parent
ASSEMBLY = HERE / "Sesame-ESP32-v122.step"

# 打印件清单：装配体零件名 → 输出文件名（对应 printing/stl/ 同名件，见 printing/README.md）
PRINT_PARTS = {
    "Internal-Frame": "Internal-Frame-v122.stp",
    "Bottom-Cover": "Bottom-Cover-v122.stp",
    "femur-joint-R1": "R1-v122.stp",
    "femurjoint-R2": "R2-v122.stp",
    "femur-joint-L1": "L1-v122.stp",
    "femur-joint-L2": "L2-v122.stp",
    "foot-joint-R3": "R3-v122.stp",
    "foot-joint-R4": "R4-v122.stp",
    "foot-joint-L3": "L3-v122.stp",
    "foot-joint-L4": "L4-v122.stp",
}


def load_labels(path: Path) -> list[tuple[str, TopoDS_Shape]]:
    """CAF 读装配，返回 [(零件名, shape)]（含每个叶节点件）。"""
    doc = TDocStd_Document(TCollection_ExtendedString("doc"))
    reader = STEPCAFControl_Reader()
    reader.SetNameMode(True)
    if reader.ReadFile(str(path)) != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise SystemExit(f"STEP 读取失败：{path}")
    reader.Transfer(doc)
    st = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())

    def name_of(lab) -> str:
        n = TDataStd_Name()
        if lab.FindAttribute(TDataStd_Name.GetID_s(), n):
            return n.Get().ToExtString()
        return "(unnamed)"

    parts: list[tuple[str, TopoDS_Shape]] = []
    seen: set[int] = set()

    def walk(lab) -> None:
        if st.IsReference_s(lab):                    # 引用：追到被引用的原件
            ref = TDF_Label()
            if st.GetReferredShape_s(lab, ref):
                walk(ref)
            return
        shp = st.GetShape_s(lab)
        if st.IsAssembly_s(lab):                     # 子装配：递归组件
            comps = Sequence_TDF_Label()
            st.GetComponents_s(lab, comps)
            for i in range(1, comps.Length() + 1):
                walk(comps.Value(i))
            return
        if shp.IsNull() or id(shp) in seen:        # 同一实体被多处引用只记一次
            return
        seen.add(id(shp))
        parts.append((name_of(lab), shp))

    tops = Sequence_TDF_Label()
    st.GetFreeShapes(tops)
    for i in range(1, tops.Length() + 1):
        walk(tops.Value(i))
    return parts


def bbox_mm(shp: TopoDS_Shape) -> tuple[float, float, float]:
    box = Bnd_Box()
    BRepBndLib.Add_s(shp, box, True)
    if box.IsVoid():
        return 0.0, 0.0, 0.0
    lo, hi = box.CornerMin(), box.CornerMax()
    return hi.X() - lo.X(), hi.Y() - lo.Y(), hi.Z() - lo.Z()


def n_solids(shp: TopoDS_Shape) -> int:
    ex = TopExp_Explorer(shp, TopAbs_SOLID)
    k = 0
    while ex.More():
        k += 1
        ex.Next()
    return k


def volume_mm3(shp: TopoDS_Shape) -> float:
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(shp, props)
    return props.Mass()


def export(shp: TopoDS_Shape, out: Path) -> None:
    from OCP.Interface import Interface_Static  # 单位/协议设置
    Interface_Static.SetCVal_s("write.step.schema", "AP214")
    Interface_Static.SetCVal_s("write.step.unit", "MM")
    writer = STEPControl_Writer()
    writer.Transfer(shp, STEPControl_StepModelType.STEPControl_AsIs)
    if writer.Write(str(out)) != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise SystemExit(f"写出失败：{out}")


def cmd_list() -> int:
    parts = load_labels(ASSEMBLY)
    print(f"{ASSEMBLY.name}：{len(parts)} 个零件\n")
    print(f"{'零件名':<44} {'实体':>4}  尺寸 X×Y×Z (mm)")
    for name, shp in parts:
        x, y, z = bbox_mm(shp)
        print(f"{name:<44} {n_solids(shp):>4}  {x:6.1f}×{y:6.1f}×{z:6.1f}")
    return 0


def cmd_split(only: list[str] | None) -> int:
    parts = dict(load_labels(ASSEMBLY))
    wanted = PRINT_PARTS if not only else {k: PRINT_PARTS[k] for k in only}
    out_dir = HERE.parent / "printing" / "stp"
    out_dir.mkdir(parents=True, exist_ok=True)
    missing = [k for k in wanted if k not in parts]
    if missing:
        print(f"[警告] 装配体里找不到：{', '.join(missing)}（用 list 查看实际名称）")
    ok = True
    for key, fname in wanted.items():
        if key not in parts:
            ok = False
            continue
        shp = parts[key]
        out = out_dir / fname
        export(shp, out)
        # 回读校验：实体数与体积一致才算成功
        back = load_labels(out)
        v0, v1 = volume_mm3(shp), sum(volume_mm3(s) for _, s in back)
        match = n_solids(shp) == sum(n_solids(s) for _, s in back) and abs(v1 - v0) < 1e-3 * max(v0, 1)
        print(f"[{'OK ' if match else 'FAIL'}] {key:<20} → printing/stp/{fname}"
              f"  {volume_mm3(shp) / 1000:.1f} cm³  实体 {n_solids(shp)}")
        ok &= match
    return 0 if ok else 1


def cmd_export(name: str, out: Path) -> int:
    parts = dict(load_labels(ASSEMBLY))
    if name not in parts:
        print(f"找不到零件 {name!r}（用 list 查看实际名称）")
        return 2
    export(parts[name], out)
    print(f"已导出 {out}")
    return 0


def main() -> int:
    for stream in (sys.stdout, sys.stderr):        # Windows 控制台 GBK 编不出部分零件名
        stream.reconfigure(encoding="utf-8", errors="replace")
    ap = argparse.ArgumentParser(description="整机 STEP 汇编 → 单件打印 STP")
    ap.add_argument("cmd", choices=["list", "split", "export"])
    ap.add_argument("--parts", nargs="*", help="split：指定零件名（缺省为全部打印件）")
    ap.add_argument("extra", nargs="*")
    args = ap.parse_args()
    if args.cmd == "list":
        return cmd_list()
    if args.cmd == "split":
        return cmd_split(args.parts)
    if len(args.extra) != 2:
        print("export 需要：export <零件名> <out.stp>")
        return 2
    return cmd_export(args.extra[0], Path(args.extra[1]))


if __name__ == "__main__":
    sys.exit(main())
