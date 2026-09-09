# -*- coding: utf-8 -*-
"""
STEP -> цветной STL / OBJ+MTL через pip-пакет OCP (OpenCASCADE), без FreeCAD.

Установка (обычный pip, никакого conda не нужно):
    pip install cadquery-ocp

Запуск:
    python step2stl_ocp.py input.stp output_base
    (output_base необязателен, по умолчанию берётся имя входного файла)

Результат: <output_base>_color.stl, <output_base>.obj, <output_base>.mtl
"""

import sys
import struct

LINEAR_DEFLECTION = 0.1   # мм; меньше = точнее и больше треугольников
ANGULAR_DEFLECTION = 0.5  # рад

def main():
    if len(sys.argv) < 2:
        print("Использование: python step2stl_ocp.py input.stp [output_base]")
        sys.exit(1)
    input_path = sys.argv[1]
    output_base = sys.argv[2] if len(sys.argv) > 2 else input_path.rsplit(".", 1)[0]

    from OCP.STEPCAFControl import STEPCAFControl_Reader
    from OCP.TDocStd import TDocStd_Document
    from OCP.XCAFApp import XCAFApp_Application
    from OCP.XCAFDoc import XCAFDoc_DocumentTool, XCAFDoc_ColorType
    from OCP.TDF import TDF_Label, TDF_LabelSequence
    from OCP.TCollection import TCollection_ExtendedString
    from OCP.Quantity import Quantity_Color
    from OCP.IFSelect import IFSelect_RetDone
    from OCP.BRepMesh import BRepMesh_IncrementalMesh
    from OCP.TopExp import TopExp_Explorer
    from OCP.TopAbs import TopAbs_FACE, TopAbs_REVERSED
    from OCP.TopoDS import TopoDS
    from OCP.BRep import BRep_Tool
    from OCP.TopLoc import TopLoc_Location

    print("Чтение STEP:", input_path)
    app = XCAFApp_Application.GetApplication_s()
    doc = TDocStd_Document(TCollection_ExtendedString("MDTV-XCAF"))
    app.NewDocument(TCollection_ExtendedString("MDTV-XCAF"), doc)

    reader = STEPCAFControl_Reader()
    reader.SetColorMode(True)
    reader.SetNameMode(True)
    status = reader.ReadFile(input_path)
    if status != IFSelect_RetDone:
        print("Не удалось прочитать файл STEP.")
        sys.exit(1)
    reader.Transfer(doc)

    shape_tool = XCAFDoc_DocumentTool.ShapeTool_s(doc.Main())
    color_tool = XCAFDoc_DocumentTool.ColorTool_s(doc.Main())

    default_color = (0.75, 0.75, 0.78)

    def get_color(label):
        c = Quantity_Color()
        for ctype in (XCAFDoc_ColorType.XCAFDoc_ColorSurf,
                      XCAFDoc_ColorType.XCAFDoc_ColorGen,
                      XCAFDoc_ColorType.XCAFDoc_ColorCurv):
            if color_tool.GetColor_s(label, ctype, c):
                return (c.Red(), c.Green(), c.Blue())
        return None

    parts = []  # {'name':str, 'shape':TopoDS_Shape, 'color':(r,g,b)}

    def walk(label, inherited_color, name_hint):
        col = get_color(label)
        if col is None and shape_tool.IsReference_s(label):
            ref = TDF_Label()
            if shape_tool.GetReferredShape_s(label, ref):
                col = get_color(ref)
        if col is None:
            col = inherited_color

        if shape_tool.IsAssembly_s(label):
            comps = TDF_LabelSequence()
            shape_tool.GetComponents_s(label, comps)
            for i in range(1, comps.Length() + 1):
                walk(comps.Value(i), col, name_hint)
        elif shape_tool.IsReference_s(label):
            ref = TDF_Label()
            shape_tool.GetReferredShape_s(label, ref)
            walk(ref, col, name_hint)
        else:
            shape = shape_tool.GetShape_s(label)
            if shape is not None and not shape.IsNull():
                parts.append({
                    "name": name_hint,
                    "shape": shape,
                    "color": col if col is not None else default_color
                })

    free_shapes = TDF_LabelSequence()
    shape_tool.GetFreeShapes(free_shapes)
    print("Найдено верхнеуровневых объектов:", free_shapes.Length())
    for i in range(1, free_shapes.Length() + 1):
        label = free_shapes.Value(i)
        walk(label, None, "Part_%d" % i)

    if not parts:
        print("Не найдено ни одной геометрии.")
        sys.exit(1)

    # ---- тесселяция ----
    all_tris = []  # (ax,ay,az, bx,by,bz, cx,cy,cz, r,g,b)
    for p in parts:
        shape = p["shape"]
        col = p["color"]
        BRepMesh_IncrementalMesh(shape, LINEAR_DEFLECTION, False, ANGULAR_DEFLECTION, True)
        exp = TopExp_Explorer(shape, TopAbs_FACE)
        n_tris_part = 0
        while exp.More():
            face = TopoDS.Face_s(exp.Current())
            loc = TopLoc_Location()
            tri = BRep_Tool.Triangulation_s(face, loc)
            if tri is not None:
                trsf = loc.Transformation()
                reversed_face = face.Orientation() == TopAbs_REVERSED
                nodes = [tri.Node(i).Transformed(trsf) for i in range(1, tri.NbNodes() + 1)]
                for t in range(1, tri.NbTriangles() + 1):
                    n1, n2, n3 = tri.Triangle(t).Get()
                    if reversed_face:
                        n2, n3 = n3, n2
                    a, b, c = nodes[n1-1], nodes[n2-1], nodes[n3-1]
                    all_tris.append((a.X(),a.Y(),a.Z(), b.X(),b.Y(),b.Z(), c.X(),c.Y(),c.Z(), col[0],col[1],col[2]))
                    n_tris_part += 1
            exp.Next()
        print("Деталь:", p["name"], " треугольников:", n_tris_part)

    print("Всего треугольников:", len(all_tris))

    # ---- цветной бинарный STL ----
    stl_path = output_base + "_color.stl"
    with open(stl_path, "wb") as f:
        header = b"ColorSTL exported from STEP via OCP"
        f.write(header + b"\x00" * (80 - len(header)))
        f.write(struct.pack("<I", len(all_tris)))
        for (ax,ay,az,bx,by,bz,cx,cy,cz,r,g,b) in all_tris:
            ux,uy,uz = bx-ax, by-ay, bz-az
            vx,vy,vz = cx-ax, cy-ay, cz-az
            nx = uy*vz - uz*vy
            ny = uz*vx - ux*vz
            nz = ux*vy - uy*vx
            ln = (nx*nx+ny*ny+nz*nz) ** 0.5 or 1.0
            nx,ny,nz = nx/ln, ny/ln, nz/ln
            r5 = min(31, max(0, round(r*31)))
            g5 = min(31, max(0, round(g*31)))
            b5 = min(31, max(0, round(b*31)))
            color_word = 0x8000 | (b5 << 10) | (g5 << 5) | r5
            f.write(struct.pack("<12fH", nx,ny,nz, ax,ay,az, bx,by,bz, cx,cy,cz, color_word))
    print("Записано:", stl_path)

    # ---- OBJ + MTL ----
    obj_lines = ["# exported from STEP via OCP", "mtllib " + output_base.split("/")[-1] + ".mtl"]
    mtl_lines = []
    mat_cache = {}
    v_idx = 0
    by_color = {}
    for tri in all_tris:
        key = tuple(round(c, 4) for c in tri[9:12])
        by_color.setdefault(key, []).append(tri)
    for key, tris in by_color.items():
        if key not in mat_cache:
            mat_name = "mat_%d" % len(mat_cache)
            mat_cache[key] = mat_name
            mtl_lines.append("newmtl " + mat_name)
            mtl_lines.append("Kd %.4f %.4f %.4f" % key)
            mtl_lines.append("Ka 0 0 0")
            mtl_lines.append("")
        obj_lines.append("usemtl " + mat_cache[key])
        for (ax,ay,az,bx,by_,bz,cx,cy,cz,r,g,b) in tris:
            obj_lines.append("v %f %f %f" % (ax,ay,az))
            obj_lines.append("v %f %f %f" % (bx,by_,bz))
            obj_lines.append("v %f %f %f" % (cx,cy,cz))
            obj_lines.append("f %d %d %d" % (v_idx+1, v_idx+2, v_idx+3))
            v_idx += 3

    with open(output_base + ".obj", "w") as f:
        f.write("\n".join(obj_lines))
    with open(output_base + ".mtl", "w") as f:
        f.write("\n".join(mtl_lines))
    print("Записано:", output_base + ".obj", "и", output_base + ".mtl")
    print("Готово.")

if __name__ == "__main__":
    main()
