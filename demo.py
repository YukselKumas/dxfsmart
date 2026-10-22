"""Create demonstration DXFs with intentional engineering problems."""
from pathlib import Path
import io
import zipfile
import ezdxf


def sample_files():
    a = ezdxf.new("R2010")
    a.header["$INSUNITS"] = 4
    m = a.modelspace()
    def plate(x, y, width=120, height=80):
        m.add_lwpolyline([(x,y),(x+width,y),(x+width,y+height),(x,y+height)], close=True)
        m.add_circle((x+width/2,y+height/2),radius=8)
    plate(0,0)
    plate(175,0)
    m.add_lwpolyline([(345,5),(435,5),(390,91)],close=True)
    m.add_circle((390,35),radius=5)
    m.add_line((475,30),(510,55))  # not part of a closed contour
    m.add_line((485,105),(520,105))
    m.add_line((485,105),(520,105))  # identical entity

    b = ezdxf.new("R2010")
    b.header["$INSUNITS"] = 4
    n = b.modelspace()
    n.add_lwpolyline([(0,0),(80,0),(80,120),(0,120)],close=True)
    n.add_circle((40,60),radius=8)
    n.add_circle((155,45),radius=30)
    n.add_lwpolyline([(230,0),(280,50),(230,50),(280,0)],close=True)  # self crossing

    return [("Plaka_Paketi_01.dxf", to_bytes(a)), ("Plaka_Paketi_02.dxf", to_bytes(b))]


def showcase_file():
    """A realistic mixed job: brackets, flanges, gussets, trapezoids, washers."""
    import math
    d = ezdxf.new("R2010")
    d.header["$INSUNITS"] = 4
    m = d.modelspace()

    def l_bracket(x, y):
        m.add_lwpolyline([(x, y), (x + 420, y), (x + 420, y + 70), (x + 70, y + 70), (x + 70, y + 320), (x, y + 320)], close=True)
        m.add_circle((x + 35, y + 270), 6.5)
        m.add_circle((x + 370, y + 35), 6.5)

    def flange(x, y):
        # outer ring drawn as two half arcs, bolt circle with 6 holes
        m.add_arc((x, y), 190, 0, 180); m.add_arc((x, y), 190, 180, 360)
        m.add_circle((x, y), 110)
        for k in range(6):
            a = math.radians(30 + 60 * k)
            m.add_circle((x + 150 * math.cos(a), y + 150 * math.sin(a)), 9)

    def gusset(x, y):
        m.add_lwpolyline([(x, y), (x + 380, y), (x + 380, y + 30), (x + 30, y + 260), (x, y + 260)], close=True)
        m.add_circle((x + 80, y + 70), 12.5)

    def trapezoid(x, y):
        m.add_lwpolyline([(x, y), (x + 520, y), (x + 430, y + 210, -0.0), (x + 90, y + 210)], close=True)
        m.add_lwpolyline([(x + 200, y + 70), (x + 320, y + 70, 1), (x + 320, y + 110), (x + 200, y + 110, 1)], close=True)  # slot

    def washer(x, y):
        m.add_circle((x, y), 42); m.add_circle((x, y), 13)

    for i in range(8):
        l_bracket((i % 4) * 500, (i // 4) * 400)
    for i in range(4):
        flange(2300 + (i % 2) * 450, 200 + (i // 2) * 450)
    for i in range(10):
        gusset((i % 5) * 450, 900 + (i // 5) * 320)
    for i in range(6):
        trapezoid((i % 3) * 600, 1600 + (i // 3) * 280)
    for i in range(14):
        washer(2300 + (i % 7) * 110, 1200 + (i // 7) * 110)
    return ("Atolye_Ornegi.dxf", to_bytes(d))


def to_bytes(doc):
    # Using ezdxf's DXF writer in text mode avoids dependence on file system.
    string = io.StringIO()
    doc.write(string)
    return string.getvalue().encode("utf-8")


def build_examples(folder):
    folder = Path(folder)
    folder.mkdir(parents=True, exist_ok=True)
    items = sample_files()
    for name, blob in items:
        (folder / name).write_bytes(blob)
    with zipfile.ZipFile(folder / "Ornek_DXF_Paketi.zip", "w", zipfile.ZIP_DEFLATED) as out:
        for name, blob in items:
            out.writestr(name, blob)
    return items


if __name__ == "__main__":
    build_examples(Path(__file__).parent / "examples")
