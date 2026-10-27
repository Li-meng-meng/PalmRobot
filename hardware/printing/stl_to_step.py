#!/usr/bin/env python3
"""stl_to_step.py — STL 网格 → STP（B-rep 多面体）转换。

用途：没有 CAD 原始文件、只有 STL 的打印件（如 Top-Cover 变体，上游仓库
只有整机装配 STEP，不含顶盖）转出 STP 供 CAD / 切片器（PrusaSlicer 2.6+
原生吃 STEP）使用。转换方式 = 三角面拼成封闭 B-rep 实体（sewing），

  ⚠ 结果是**多面体 B-rep**：每个三角形是一张平面片，不是光顺曲面。
  改结构请优先用 cad/ 里的原始装配 STEP；本工具是无源文件时的兜底。

处理细节：
  - 顶点按 1e-4 mm 网格去重 → 每条边严格共享 → 可缝合；
  - 重复三角形（同顶点集）只保留一份——Top-Cover v117 已知的
    "一条边被 4 个三角形共享"瑕疵由此修复；
  - ShapeFix_Solid 封闭 + 体积对照网格符号体积（偏差 >1% 判 FAIL）。

用法：
  python stl_to_step.py <in.stl> [out.stp]
  python stl_to_step.py stl/*.stl           # 批量，输出到同目录 .stp
"""
from __future__ import annotations

import struct
import sys
from pathlib import Path

import numpy as np

from OCP.BRep import BRep_Builder
from OCP.BRepBuilderAPI import (
    BRepBuilderAPI_MakeFace,
    BRepBuilderAPI_MakePolygon,
    BRepBuilderAPI_Sewing,
)
from OCP.IFSelect import IFSelect_ReturnStatus
from OCP.Interface import Interface_Static
from OCP.ShapeFix import ShapeFix_Solid
from OCP.STEPControl import STEPControl_StepModelType, STEPControl_Writer
from OCP.TopoDS import TopoDS_Compound

TOL_VERT = 1e-4      # 顶点去重网格（mm）——STL 二进制是 float32，同点误差远小于此
SEW_TOL = 0.005      # 缝合容差（mm）


def _is_num(x: str) -> bool:
    try:
        float(x)
        return True
    except ValueError:
        return False


def read_stl(path: Path) -> np.ndarray:
    """STL（二进制/文本）→ (n, 3, 3) float64 三角面片（mm）。"""
    raw = path.read_bytes()
    if raw[:5] == b"solid" and b"facet" in raw[:512]:     # 文本 STL
        toks = raw.decode("ascii", errors="ignore").split()
        coords = [float(x) for x in toks[toks.index("vertex"):] if _is_num(x)]
        n = len(coords) // 9
        return np.array(coords[: n * 9], dtype=np.float64).reshape(n, 3, 3)
    n = struct.unpack_from("<I", raw, 80)[0]
    rec = np.frombuffer(raw, dtype=np.uint8, count=n * 50, offset=84).reshape(n, 50)
    f32 = np.ascontiguousarray(rec[:, :48]).view("<f4").reshape(n, 12)  # 50B 记录非 4 对齐
    return f32[:, 3:].reshape(n, 3, 3).astype(np.float64)               # 跳过法线


def mesh_volume(tris: np.ndarray) -> float:
    """网格符号体积（mm³）。"""
    v0, v1, v2 = tris[:, 0], tris[:, 1], tris[:, 2]
    return float(np.einsum("ij,ij->i", v0, np.cross(v1, v2)).sum() / 6.0)


def mesh_to_solid(tris: np.ndarray):
    """三角面片 → 缝合 B-rep 实体。返回 (TopoDS_Solid, 诊断 dict)。"""
    from OCP.gp import gp_Pnt

    # 顶点去重：float32 打印件同点误差 < 1e-5 mm，1e-4 网格安全
    flat = tris.reshape(-1, 3)
    keys = np.round(flat / TOL_VERT).astype(np.int64)
    uniq, inv = np.unique(keys, axis=0, return_inverse=True)
    inv = inv.reshape(len(tris), 3)
    # 重复三角形去重（同顶点集，顺序无关）：保留排序后的首现
    tri_sorted = np.sort(inv, axis=1)
    _, tri_idx = np.unique(tri_sorted, axis=0, return_index=True)
    keep = np.sort(tri_idx)
    dropped = len(tris) - len(keep)
    inv = inv[keep]

    pts = uniq * TOL_VERT                               # 去重后顶点坐标
    mk_face, mk_poly = BRepBuilderAPI_MakeFace, BRepBuilderAPI_MakePolygon
    builder = BRep_Builder()
    comp = TopoDS_Compound()
    builder.MakeCompound(comp)
    for a, b, c in inv:                                 # 三角形 → 平面面片
        poly = mk_poly(gp_Pnt(*pts[a]), gp_Pnt(*pts[b]), gp_Pnt(*pts[c]), True)
        builder.Add(comp, mk_face(poly.Wire()).Face())

    sew = BRepBuilderAPI_Sewing(SEW_TOL, True, True, True, False)
    sew.Add(comp)
    sew.Perform()
    sewed = sew.SewedShape()

    # 缝合结果可能是单个 Shell 或含多个 Shell 的 Compound：逐壳封闭成实体
    from OCP.TopAbs import TopAbs_SHELL
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopoDS import TopoDS
    fix = ShapeFix_Solid()
    ex = TopExp_Explorer(sewed, TopAbs_SHELL)
    solids = []
    while ex.More():
        solids.append(fix.SolidFromShell(TopoDS.Shell(ex.Current())))
        ex.Next()
    if not solids:                                    # 一片壳都没缝出来
        diag = {"triangles_in": len(tris), "triangles_used": len(keep),
                "dup_dropped": dropped, "vertices": len(uniq), "shells": 0}
        return comp, diag
    if len(solids) == 1:
        result = solids[0]
    else:                                             # 缝出多壳：装进复合体（体积求和仍有效）
        result = TopoDS_Compound()
        builder.MakeCompound(result)
        for s in solids:
            builder.Add(result, s)
    diag = {"triangles_in": len(tris), "triangles_used": len(keep),
            "dup_dropped": dropped, "vertices": len(uniq), "shells": len(solids)}
    return result, diag


def to_step(solid, out: Path) -> None:
    Interface_Static.SetCVal_s("write.step.schema", "AP214")
    Interface_Static.SetCVal_s("write.step.unit", "MM")
    writer = STEPControl_Writer()
    writer.Transfer(solid, STEPControl_StepModelType.STEPControl_AsIs)
    if writer.Write(str(out)) != IFSelect_ReturnStatus.IFSelect_RetDone:
        raise SystemExit(f"写出失败：{out}")


def solid_volume(solid) -> float:
    from OCP.BRepGProp import BRepGProp
    from OCP.GProp import GProp_GProps
    props = GProp_GProps()
    BRepGProp.VolumeProperties_s(solid, props)
    return props.Mass()


def convert(src: Path, dst: Path) -> int:
    tris = read_stl(src)
    vmesh = mesh_volume(tris)
    solid, diag = mesh_to_solid(tris)
    vsolid = solid_volume(solid)
    dev = abs(vsolid - abs(vmesh)) / max(abs(vmesh), 1e-9)
    to_step(solid, dst)
    ok = dev < 0.01
    print(f"[{'OK ' if ok else 'FAIL'}] {src.name} → {dst.name}  "
          f"{diag['triangles_in']} 三角形(去重{diag['dup_dropped']}) "
          f"{diag['vertices']} 顶点  体积 {vsolid / 1000:.1f} cm³ "
          f"(网格 {abs(vmesh) / 1000:.1f}, 偏差 {dev * 100:.2f}%)")
    return 0 if ok else 1


def main() -> int:
    for stream in (sys.stdout, sys.stderr):
        stream.reconfigure(encoding="utf-8", errors="replace")
    args = [a for a in sys.argv[1:]]
    if not args:
        print(__doc__)
        return 2
    if len(args) == 2 and not Path(args[0]).is_dir():
        pairs = [(Path(args[0]), Path(args[1]))]
    else:
        pairs = [(Path(a), Path(a).with_suffix(".stp")) for a in args]
    rc = 0
    for src, dst in pairs:
        rc |= convert(src, dst)
    return rc


if __name__ == "__main__":
    sys.exit(main())
