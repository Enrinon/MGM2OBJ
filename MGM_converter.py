#!/usr/bin/env python3
"""
Extractor visual para stage*.mgm del juego Madoka Magica Mami TPS.

Lee únicamente:
  - cabecera y materiales
  - mallas de las regiones
  - malla de fondo
  - registros JumpPoint (se leen para mantener la alineación, no se exportan)

No interpreta CollisionFace ni los datos de enemigos.
Exporta OBJ + MTL. Las posiciones se escalan por 0.1, igual que FieldRegion.smali.
"""

from __future__ import annotations
import argparse
import math
import shutil
import struct
import sys
from dataclasses import dataclass
from pathlib import Path


class ParseError(Exception):
    pass


class Reader:
    def __init__(self, data: bytes):
        self.data = data
        self.pos = 0

    def require(self, size: int, what: str = "datos") -> None:
        if size < 0 or self.pos + size > len(self.data):
            raise ParseError(
                f"Archivo truncado al leer {what}: offset 0x{self.pos:X}, "
                f"se necesitan {size} bytes, quedan {len(self.data)-self.pos}."
            )

    def read_i32(self, what: str = "int32") -> int:
        self.require(4, what)
        value = struct.unpack_from("<i", self.data, self.pos)[0]
        self.pos += 4
        return value

    def read_floats(self, count: int, what: str) -> tuple[float, ...]:
        if count < 0 or count > 3_000_000:
            raise ParseError(f"Cantidad de floats no válida para {what}: {count}")
        size = count * 4
        self.require(size, what)
        if count == 0:
            return ()
        values = struct.unpack_from("<" + "f" * count, self.data, self.pos)
        self.pos += size
        if not all(math.isfinite(v) for v in values):
            raise ParseError(f"Se encontraron NaN/Inf en {what}.")
        return values

    def read_string(self, length: int) -> str:
        if length < 0 or length > 4096:
            raise ParseError(f"Longitud de nombre no válida: {length}")
        self.require(length, "nombre de material")
        raw = self.data[self.pos:self.pos + length]
        self.pos += length
        try:
            return raw.decode("shift_jis")
        except UnicodeDecodeError as exc:
            raise ParseError(f"Nombre Shift-JIS inválido en offset 0x{self.pos-length:X}: {exc}")


@dataclass
class Material:
    name: str
    kind: int
    u_scroll: float
    v_scroll: float


@dataclass
class Mesh:
    vertex_count: int
    positions: tuple[float, ...]
    uvs: tuple[float, ...]
    colors: tuple[float, ...]


@dataclass
class Region:
    meshes: list[Mesh]
    jump_points: list[tuple[int, tuple[float, ...], tuple[float, ...]]]


def parse_region(reader: Reader, material_count: int, label: str) -> Region:
    meshes = []
    jumps = []
    for material_index in range(material_count):
        count = reader.read_i32(f"{label}, cantidad de vértices del material {material_index}")
        if count < 0 or count > 500_000:
            raise ParseError(
                f"{label}: vertexCount={count} no es razonable para el material {material_index}."
            )
        # FieldRegion.smali: posiciones (3), UV (2), color RGBA (4) por vértice.
        positions = reader.read_floats(count * 3, f"{label}, posiciones material {material_index}")
        uvs = reader.read_floats(count * 2, f"{label}, UV material {material_index}")
        colors = reader.read_floats(count * 4, f"{label}, colores material {material_index}")
        if count % 3:
            raise ParseError(
                f"{label}, material {material_index}: {count} vértices; "
                "GL_TRIANGLES requiere grupos de 3."
            )
        meshes.append(Mesh(count, positions, uvs, colors))

    jump_count = reader.read_i32(f"{label}, cantidad de JumpPoint")
    if jump_count < 0 or jump_count > 100_000:
        raise ParseError(f"{label}: cantidad de JumpPoint no válida: {jump_count}")
    for j in range(jump_count):
        region_id = reader.read_i32(f"{label}, JumpPoint {j}, region")
        start = reader.read_floats(3, f"{label}, JumpPoint {j}, startPos")
        end = reader.read_floats(3, f"{label}, JumpPoint {j}, endPos")
        # Se consumen 28 bytes por JumpPoint. No forman parte de la malla renderizada.
        jumps.append((region_id, start, end))
    return Region(meshes, jumps)


def safe_name(name: str) -> str:
    cleaned = "".join(c if c.isalnum() or c in "-_." else "_" for c in name)
    return cleaned or "texture"


def find_texture(texture_dir: Path | None, base_name: str) -> Path | None:
    if texture_dir is None or not texture_dir.is_dir():
        return None
    # Prioriza extensiones habituales de recursos Android.
    for ext in (".png", ".jpg", ".jpeg", ".webp", ".bmp"):
        candidate = texture_dir / (base_name + ext)
        if candidate.is_file():
            return candidate
    # También acepta nombres con mayúsculas/minúsculas diferentes.
    wanted = base_name.casefold()
    for candidate in texture_dir.iterdir():
        if candidate.is_file() and candidate.stem.casefold() == wanted:
            return candidate
    return None


def parse_file(path: Path):
    data = path.read_bytes()
    reader = Reader(data)

    material_count = reader.read_i32("materialNum")
    region_count = reader.read_i32("regionNum")
    if not (1 <= material_count <= 256):
        raise ParseError(f"materialNum fuera de rango: {material_count}")
    if not (0 <= region_count <= 10_000):
        raise ParseError(f"regionNum fuera de rango: {region_count}")

    materials = []
    for i in range(material_count):
        name_len = reader.read_i32(f"material {i}, nameLen")
        name = reader.read_string(name_len)
        kind = reader.read_i32(f"material {i}, materialType")
        u_scroll = reader.read_i32(f"material {i}, U scroll") * 0.001
        v_scroll = reader.read_i32(f"material {i}, V scroll") * 0.001
        materials.append(Material(name, kind, u_scroll, v_scroll))

    regions = []
    for i in range(region_count):
        regions.append(parse_region(reader, material_count, f"region_{i}"))

    # GameField.create() carga una FieldRegion adicional con índice -1: bgRegion.
    background = parse_region(reader, material_count, "background")

    # El siguiente int es collisionNum. Solo lo leemos como marcador de diagnóstico;
    # no parseamos ni exportamos ninguna colisión.
    collision_offset = reader.pos
    collision_count = reader.read_i32("collisionNum (marcador posterior a los visuales)")

    return {
        "data_size": len(data),
        "material_count": material_count,
        "region_count": region_count,
        "materials": materials,
        "regions": regions,
        "background": background,
        "visual_end_offset": collision_offset,
        "collision_count": collision_count,
        "reader_after_collision_count": reader.pos,
    }


def export_obj(parsed, output_obj: Path, texture_dir: Path | None, vertex_colors: bool):
    output_obj.parent.mkdir(parents=True, exist_ok=True)
    mtl_path = output_obj.with_suffix(".mtl")
    output_dir = output_obj.parent

    materials: list[Material] = parsed["materials"]
    obj_lines = [
        "# Visual mesh extracted from stage*.mgm",
        "# Positions scaled by 0.1 as in GameField$FieldRegion.smali",
        f"mtllib {mtl_path.name}",
        "",
    ]
    mtl_lines = ["# Materials from stage*.mgm", ""]
    copied_textures = []
    missing_textures = []

    for i, material in enumerate(materials):
        mat_id = f"mat_{i:02d}_{safe_name(material.name)}"
        mtl_lines += [
            f"newmtl {mat_id}",
            "Ka 1.000000 1.000000 1.000000",
            "Kd 1.000000 1.000000 1.000000",
            "Ks 0.000000 0.000000 0.000000",
            "d 1.000000",
            "illum 1",
        ]
        texture = find_texture(texture_dir, material.name)
        if texture:
            destination = output_dir / texture.name
            if texture.resolve() != destination.resolve():
                shutil.copy2(texture, destination)
            mtl_lines.append(f"map_Kd {destination.name}")
            copied_textures.append(destination.name)
        else:
            # Se deja el nombre esperado para que pueda vincularse manualmente después.
            mtl_lines.append(f"# map_Kd {material.name} (textura no encontrada)")
            missing_textures.append(material.name)
        mtl_lines.append("")

    vertex_base = 1
    uv_base = 1
    exported_meshes = 0
    total_vertices = 0
    total_triangles = 0

    # Las regiones normales primero; el fondo se conserva como un grupo separado.
    all_regions = [(f"region_{i}", region) for i, region in enumerate(parsed["regions"])]
    all_regions.append(("background", parsed["background"]))

    for region_name, region in all_regions:
        obj_lines += [f"g {region_name}", ""]
        for mat_index, mesh in enumerate(region.meshes):
            if mesh.vertex_count == 0:
                continue
            material = materials[mat_index]
            mat_id = f"mat_{mat_index:02d}_{safe_name(material.name)}"
            obj_lines += [f"usemtl {mat_id}", f"# {mesh.vertex_count} vertices"]
            count = mesh.vertex_count

            for v in range(count):
                x, y, z = mesh.positions[v*3:v*3+3]
                x *= 0.1
                y *= 0.1
                z *= 0.1
                if vertex_colors:
                    r, g, b, _a = mesh.colors[v*4:v*4+4]
                    obj_lines.append(f"v {x:.8g} {y:.8g} {z:.8g} {r:.8g} {g:.8g} {b:.8g}")
                else:
                    obj_lines.append(f"v {x:.8g} {y:.8g} {z:.8g}")

            for v in range(count):
                u, vv = mesh.uvs[v*2:v*2+2]
                obj_lines.append(f"vt {u:.8g} {vv:.8g}")

            for tri in range(0, count, 3):
                a = vertex_base + tri
                b = vertex_base + tri + 1
                c = vertex_base + tri + 2
                ta = uv_base + tri
                tb = uv_base + tri + 1
                tc = uv_base + tri + 2
                obj_lines.append(f"f {a}/{ta} {b}/{tb} {c}/{tc}")

            obj_lines.append("")
            vertex_base += count
            uv_base += count
            total_vertices += count
            total_triangles += count // 3
            exported_meshes += 1

    output_obj.write_text("\n".join(obj_lines) + "\n", encoding="utf-8")
    mtl_path.write_text("\n".join(mtl_lines) + "\n", encoding="utf-8")

    return {
        "obj": str(output_obj),
        "mtl": str(mtl_path),
        "meshes_with_geometry": exported_meshes,
        "vertices": total_vertices,
        "triangles": total_triangles,
        "copied_textures": copied_textures,
        "missing_textures": sorted(set(missing_textures)),
    }


def main():
    parser = argparse.ArgumentParser(
        description="Convierte la geometría visual de stage*.mgm a OBJ/MTL."
    )
    parser.add_argument("input", type=Path, help="Archivo stage*.mgm")
    parser.add_argument("-o", "--output", type=Path, default=None, help="OBJ de salida")
    parser.add_argument(
        "--textures",
        type=Path,
        default=None,
        help="Carpeta con texturas extraídas (sky21.png, image47.png, etc.)",
    )
    parser.add_argument(
        "--no-vertex-colors",
        action="store_true",
        help="No escribir RGB como extensión no estándar de OBJ.",
    )
    args = parser.parse_args()

    if not args.input.is_file():
        parser.error(f"No existe el archivo: {args.input}")
    output = args.output or args.input.with_name(args.input.stem + "_visual.obj")

    try:
        parsed = parse_file(args.input)
        exported = export_obj(parsed, output, args.textures, not args.no_vertex_colors)
    except (ParseError, OSError, struct.error) as exc:
        print(f"ERROR: {exc}", file=sys.stderr)
        return 2

    print("=== Extracción visual completada ===")
    print(f"Archivo: {args.input}")
    print(f"Tamaño: {parsed['data_size']} bytes")
    print(f"Materiales: {parsed['material_count']}")
    print(f"Regiones normales: {parsed['region_count']}")
    for i, region in enumerate(parsed["regions"]):
        verts = sum(m.vertex_count for m in region.meshes)
        print(f"  region_{i}: {verts} vértices, {len(region.jump_points)} JumpPoint")
    bg = parsed["background"]
    print(f"  background: {sum(m.vertex_count for m in bg.meshes)} vértices")
    print(f"Fin de datos visuales: 0x{parsed['visual_end_offset']:X}")
    print(f"collisionNum (solo marcador): {parsed['collision_count']}")
    print(f"OBJ: {exported['obj']}")
    print(f"MTL: {exported['mtl']}")
    print(f"Mallas exportadas: {exported['meshes_with_geometry']}")
    print(f"Vértices: {exported['vertices']}")
    print(f"Triángulos: {exported['triangles']}")
    if args.textures:
        print(f"Texturas copiadas: {len(exported['copied_textures'])}")
        if exported["missing_textures"]:
            print("Texturas no encontradas: " + ", ".join(exported["missing_textures"]))
    else:
        print("Texturas: no se indicó --textures; el MTL queda preparado para enlazarlas.")
    print("Nota: los colores RGB se guardan como extensión OBJ no estándar; usa --no-vertex-colors para OBJ estricto.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
