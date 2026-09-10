"""
Лёгкий 3D-просмотрщик для больших STL/OBJ/PLY/STEP-файлов, которые не
помещаются в браузер. Работает через VTK (библиотека PyVista) — она
рисует геометрию через видеокарту, а не через JS-кучу, поэтому спокойно
тянет файлы в разы больше, чем браузерный движок.

УСТАНОВКА (один раз, в консоли/терминале):
    pip install pyvista numpy

Для STEP-файлов дополнительно нужен модуль с CAD-ридером:
    pip install cadquery-ocp

ЗАПУСК:
    python view3d.py "путь/к/детали.stl"

Полезные флаги:
    --max-tris 2000000    сколько треугольников оставить при прореживании
                           (по умолчанию 2 000 000 — плавно вертится почти
                           на любом ПК; для файлов меньше этого лимита
                           прореживание не применяется)
    --no-decimate          не прореживать вообще (может быть очень медленно
                           / не хватит памяти на действительно огромных файлах)
    --save-decimated OUT.stl
                           сохранить прорежённую версию на диск, чтобы потом
                           открывать её мгновенно (и можно закинуть обратно
                           в браузерный просмотрщик)
    --color R G B          залить деталь одним цветом (0-255 каждое), если
                           не нужен цвет из файла
    --free-camera          включить свободную камеру (WASD + мышь), как в
                           браузерном просмотрщике, вместо обычной орбитальной

Управление свободной камерой (--free-camera):
    W/A/S/D       — движение вперёд/влево/назад/вправо
    Q/E           — вниз/вверх
    Shift         — ускорение
    ПКМ + мышь    — обзор (зажать правую кнопку и двигать мышь)
    колесо мыши   — скорость полёта
    Esc           — вернуться к обычной орбитальной камере
"""
import sys
import time
import argparse
import numpy as np
import pyvista as pv
import vtk


def load_mesh(path):
    print(f"Загружаю {path} ...")
    ext = path.lower().rsplit(".", 1)[-1]
    if ext in ("step", "stp"):
        # STEP — не полигональная сетка, а точная геометрия; тесселируем через OCP.
        try:
            from OCP.STEPControl import STEPControl_Reader
            from OCP.IFSelect import IFSelect_RetDone
            from OCP.BRepMesh import BRepMesh_IncrementalMesh
            from OCP.TopExp import TopExp_Explorer
            from OCP.TopAbs import TopAbs_FACE
            from OCP.BRep import BRep_Tool
            from OCP.TopLoc import TopLoc_Location

            reader = STEPControl_Reader()
            if reader.ReadFile(path) != IFSelect_RetDone:
                raise RuntimeError("Не удалось прочитать STEP-файл")
            reader.TransferRoots()
            shape = reader.OneShape()
            BRepMesh_IncrementalMesh(shape, 0.5)  # точность тесселяции, мм

            verts, faces = [], []
            exp = TopExp_Explorer(shape, TopAbs_FACE)
            while exp.More():
                face = exp.Current()
                loc = TopLoc_Location()
                tri = BRep_Tool.Triangulation_s(face, loc)
                if tri is not None:
                    base = len(verts)
                    trsf = loc.Transformation()
                    for i in range(1, tri.NbNodes() + 1):
                        p = tri.Node(i).Transformed(trsf)
                        verts.append((p.X(), p.Y(), p.Z()))
                    for i in range(1, tri.NbTriangles() + 1):
                        a, b, c = tri.Triangle(i).Get()
                        faces.append((3, base + a - 1, base + b - 1, base + c - 1))
                exp.Next()
            points = np.array(verts, dtype=float)
            cells = np.hstack(faces).astype(int)
            return pv.PolyData(points, cells)
        except ImportError:
            print("Для STEP нужен пакет: pip install cadquery-ocp")
            sys.exit(1)
    return pv.read(path)


class FreeFlyCameraStyle(vtk.vtkInteractorStyleUser):
    """
    Свободная камера (WASD + обзор мышью), как в браузерном 3D-просмотрщике.
    W/A/S/D — движение, Q/E — вниз/вверх, Shift — ускорение,
    ПКМ + движение мыши — обзор, колесо — скорость полёта.
    """
    def __init__(self, renderer, on_escape=None, base_speed=10.0):
        super().__init__()
        self.renderer = renderer
        self.camera = renderer.GetActiveCamera()
        self.on_escape = on_escape
        self.base_speed = base_speed
        self.speed_mult = 1.0
        self.keys = set()
        self.look_dragging = False
        self.last_mouse = (0, 0)
        self.yaw = 0.0
        self.pitch = 0.0
        self._sync_angles_from_camera()

        self.AddObserver("KeyPressEvent", self._on_key_press)
        self.AddObserver("KeyReleaseEvent", self._on_key_release)
        self.AddObserver("RightButtonPressEvent", self._on_right_down)
        self.AddObserver("RightButtonReleaseEvent", self._on_right_up)
        self.AddObserver("MouseMoveEvent", self._on_mouse_move)
        self.AddObserver("MouseWheelForwardEvent", self._on_wheel_fwd)
        self.AddObserver("MouseWheelBackwardEvent", self._on_wheel_back)

    def _sync_angles_from_camera(self):
        fp = np.array(self.camera.GetFocalPoint())
        pos = np.array(self.camera.GetPosition())
        d = fp - pos
        dist = np.linalg.norm(d)
        d = d / dist if dist > 1e-6 else np.array([0.0, 0.0, -1.0])
        self.yaw = float(np.degrees(np.arctan2(d[0], -d[2])))
        self.pitch = float(np.degrees(np.arcsin(np.clip(d[1], -1, 1))))

    def _on_key_press(self, obj, evt):
        key = (self.GetInteractor().GetKeySym() or "").lower()
        self.keys.add(key)
        if key == "escape" and self.on_escape:
            self.on_escape()

    def _on_key_release(self, obj, evt):
        key = (self.GetInteractor().GetKeySym() or "").lower()
        self.keys.discard(key)

    def _on_right_down(self, obj, evt):
        self.look_dragging = True
        self.last_mouse = self.GetInteractor().GetEventPosition()

    def _on_right_up(self, obj, evt):
        self.look_dragging = False

    def _on_mouse_move(self, obj, evt):
        iren = self.GetInteractor()
        x, y = iren.GetEventPosition()
        if self.look_dragging:
            lx, ly = self.last_mouse
            dx, dy = x - lx, y - ly
            sens = 0.15
            self.yaw -= dx * sens
            self.pitch = float(np.clip(self.pitch - dy * sens, -89, 89))
            self._apply_look()
        self.last_mouse = (x, y)

    def _on_wheel_fwd(self, obj, evt):
        self.speed_mult = min(20.0, self.speed_mult * 1.2)

    def _on_wheel_back(self, obj, evt):
        self.speed_mult = max(0.05, self.speed_mult / 1.2)

    def _forward_right_up(self):
        yaw_r, pitch_r = np.radians(self.yaw), np.radians(self.pitch)
        fwd = np.array([
            np.sin(yaw_r) * np.cos(pitch_r),
            np.sin(pitch_r),
            -np.cos(yaw_r) * np.cos(pitch_r),
        ])
        world_up = np.array([0.0, 1.0, 0.0])
        right = np.cross(fwd, world_up)
        n = np.linalg.norm(right)
        right = right / n if n > 1e-6 else np.array([1.0, 0.0, 0.0])
        up = np.cross(right, fwd)
        return fwd, right, up

    def _apply_look(self):
        fwd, _, _ = self._forward_right_up()
        pos = np.array(self.camera.GetPosition())
        self.camera.SetFocalPoint(*(pos + fwd))
        self.camera.SetViewUp(0, 1, 0)

    def step(self, dt):
        if not self.keys:
            return
        fwd, right, up = self._forward_right_up()
        move = np.zeros(3)
        if "w" in self.keys: move += fwd
        if "s" in self.keys: move -= fwd
        if "d" in self.keys: move += right
        if "a" in self.keys: move -= right
        if "e" in self.keys or "space" in self.keys: move += np.array([0.0, 1.0, 0.0])
        if "q" in self.keys: move -= np.array([0.0, 1.0, 0.0])
        n = np.linalg.norm(move)
        if n < 1e-9:
            return
        move = move / n
        boost = 4.0 if ("shift_l" in self.keys or "shift_r" in self.keys or "shift" in self.keys) else 1.0
        speed = self.base_speed * self.speed_mult * boost
        pos = np.array(self.camera.GetPosition()) + move * speed * dt
        self.camera.SetPosition(*pos)
        self.camera.SetFocalPoint(*(pos + fwd))
        self.camera.SetViewUp(0, 1, 0)


def enable_free_camera(plotter, base_speed):
    """Подключает свободную камеру и возвращает функцию для возврата к обычной орбитальной."""
    ren = plotter.renderer
    iren = plotter.iren.interactor
    orbit_style = iren.GetInteractorStyle()

    def restore_orbit():
        iren.SetInteractorStyle(orbit_style)
        print("Свободная камера выключена — обычная орбитальная камера.")

    fly_style = FreeFlyCameraStyle(ren, on_escape=restore_orbit, base_speed=base_speed)
    iren.SetInteractorStyle(fly_style)

    last_time = [time.time()]

    def on_timer(obj, evt):
        now = time.time()
        dt = now - last_time[0]
        last_time[0] = now
        fly_style.step(dt)
        plotter.render()

    iren.AddObserver("TimerEvent", on_timer)
    iren.CreateRepeatingTimer(16)
    print("Свободная камера: WASD — движение, Q/E — вниз/вверх, Shift — ускорение, "
          "ПКМ+мышь — обзор, колесо — скорость, Esc — выйти в орбитальную камеру.")
    return fly_style


def main():
    ap = argparse.ArgumentParser(description=__doc__, formatter_class=argparse.RawDescriptionHelpFormatter)
    ap.add_argument("file", help="путь к STL/OBJ/PLY/STEP файлу")
    ap.add_argument("--max-tris", type=int, default=2_000_000)
    ap.add_argument("--no-decimate", action="store_true")
    ap.add_argument("--save-decimated", default=None)
    ap.add_argument("--color", nargs=3, type=int, default=None, metavar=("R", "G", "B"))
    ap.add_argument("--free-camera", action="store_true", help="включить свободную камеру (WASD + мышь)")
    args = ap.parse_args()

    mesh = load_mesh(args.file)
    print(f"Треугольников: {mesh.n_cells:,}   Точек: {mesh.n_points:,}")

    if not args.no_decimate and mesh.n_cells > args.max_tris:
        frac_to_keep = args.max_tris / mesh.n_cells
        print(f"Модель большая — прорежаю до ~{args.max_tris:,} треугольников "
              f"(оставляю {frac_to_keep*100:.1f}%)...")
        mesh = mesh.triangulate()
        mesh = mesh.decimate(1 - frac_to_keep)
        print(f"После прореживания: {mesh.n_cells:,} треугольников")

    if args.save_decimated:
        mesh.save(args.save_decimated)
        print(f"Сохранено: {args.save_decimated}")

    plotter = pv.Plotter()
    if args.color:
        plotter.add_mesh(mesh, color=tuple(c / 255 for c in args.color),
                          smooth_shading=True, show_edges=False)
    elif "RGB" in mesh.point_data or "RGB" in mesh.cell_data:
        plotter.add_mesh(mesh, scalars="RGB", rgb=True, smooth_shading=True)
    else:
        plotter.add_mesh(mesh, color="lightgray", smooth_shading=True, show_edges=False)

    plotter.add_axes()
    plotter.show_grid()

    if args.free_camera:
        # Базовую скорость полёта подгоняем под размер модели (как в браузерном просмотрщике).
        bounds = mesh.bounds  # (xmin,xmax, ymin,ymax, zmin,zmax)
        diag = np.linalg.norm([bounds[1] - bounds[0], bounds[3] - bounds[2], bounds[5] - bounds[4]])
        base_speed = max(diag / 20.0, 0.001)
        plotter.show(title=f"3D просмотрщик — {args.file}", auto_close=False, interactive_update=True)
        enable_free_camera(plotter, base_speed)
        plotter.iren.interactor.Start()
    else:
        plotter.show(title=f"3D просмотрщик — {args.file}")


if __name__ == "__main__":
    main()
