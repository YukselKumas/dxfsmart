import io
import unittest
import ezdxf
from engine import analyze_dxf,pack_parts
import zipfile

class PackageDrawingTests(unittest.TestCase):
    def test_frames_bends_split_thickness_and_label_counts(self):
        doc=ezdxf.new('R2010'); doc.units=4; m=doc.modelspace()
        for i,quantity in enumerate((10,24,57)):
            x=i*300
            m.add_lwpolyline([(x,0),(x+300,0),(x+300,300),(x,300)],close=True)
            # A rectangular blank must remain, unlike its enclosing frame.
            m.add_lwpolyline([(x+40,80),(x+200,80),(x+200,240),(x+40,240)],close=True)
            m.add_line((x+40,160),(x+200,160))
            m.add_text(f'{quantity} ADET',dxfattribs={'insert':(x+40,20),'height':12})
            block=doc.blocks.new(f'thickness{i}')
            block.add_text('2',dxfattribs={'insert':(0,0),'height':12})
            block.add_text('MM',dxfattribs={'insert':(20,0),'height':12})
            m.add_blockref(block.name,(x+40,45))
        stream=io.StringIO();doc.write(stream)
        result=analyze_dxf(stream.getvalue().encode(),'drawing.dxf',.05)
        self.assertEqual(len(result.parts),3)
        self.assertEqual([p.quantity for p in result.parts],[10,24,57])
        self.assertTrue(all(p.thickness_mm==2 for p in result.parts))
        self.assertTrue(all(abs(p.shape.area-25600)<.1 for p in result.parts))
        self.assertTrue(all(p.needs_review for p in result.parts))
        with zipfile.ZipFile(io.BytesIO(pack_parts(result.parts))) as archive:
            self.assertEqual(sum(n.endswith('.dxf') for n in archive.namelist()),3)
            self.assertIn('Parca_Listesi.csv',archive.namelist())

if __name__=='__main__': unittest.main()
