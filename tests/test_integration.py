"""Exercise uploaded demo ZIP, DXF fidelity, duplicates and rectangle planning."""
import io
import zipfile
import unittest

import ezdxf
from fastapi.testclient import TestClient
from app import app
from demo import sample_files
from estimate import estimate_sheets
from engine import Part, ProcessingError
from shapely.geometry import Polygon


class EndToEndTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        cls.client=TestClient(app)

    def test_demo_upload_and_export(self):
        response=self.client.get('/api/demo')
        self.assertEqual(response.status_code, 200)
        analysis=response.json()
        self.assertEqual(analysis['summary']['parts'], 5)
        self.assertEqual(analysis['summary']['files'], 2)
        self.assertGreaterEqual(analysis['summary']['duplicateGroups'], 1)
        self.assertGreater(analysis['summary']['warnings'], 0)
        z=self.client.get('/api/download/'+analysis['jobId'])
        self.assertEqual(z.status_code, 200)
        with zipfile.ZipFile(io.BytesIO(z.content)) as archive:
            dxfs=sorted(n for n in archive.namelist() if n.endswith('.dxf'))
            self.assertEqual(len(dxfs), 5)
            for name in dxfs:
                doc=ezdxf.read(io.StringIO(archive.read(name).decode('utf-8')))
                self.assertGreater(len(doc.modelspace()), 0)
                self.assertEqual(doc.header['$INSUNITS'],4)
            self.assertEqual({e.dxftype() for e in ezdxf.read(io.StringIO(archive.read(dxfs[0]).decode())).modelspace()}, {'LWPOLYLINE','CIRCLE'})

    def test_zip_post(self):
        with zipfile.ZipFile(io.BytesIO(), 'w') as unused:
            pass
        stream=io.BytesIO()
        with zipfile.ZipFile(stream,'w',zipfile.ZIP_DEFLATED) as zip_out:
            for name,data in sample_files(): zip_out.writestr(name,data)
        upload=self.client.post('/api/analyze',files={'files':('batch.zip',stream.getvalue(),'application/zip')})
        self.assertEqual(upload.status_code,200)
        self.assertEqual(upload.json()['summary']['parts'],5)

    def test_packing_non_overlap(self):
        p=Part(id='rect', filename='plate.dxf', index=1,
               shape=Polygon([(0,0),(100,0),(100,50),(0,50)]),
               entity_refs=[],units=4)
        e=estimate_sheets({'rect':p},{'sheetWidth':300,'sheetHeight':200,
           'gap':5,'margin':10,'targetEfficiency':80,'allowRotation':True,
           'items':[{'id':'rect','quantity':16}]})
        self.assertEqual(e['totalQuantity'],16)
        self.assertGreaterEqual(e['recommendedSheets'], e['packingSheets'])
        self.assertEqual(sum(s['quantity'] for s in e['sheets']),16)
        for s in e['sheets']:
            loc=s['placements']
            for i,a in enumerate(loc):
                self.assertGreaterEqual(a['x'],10)
                self.assertGreaterEqual(a['y'],10)
                self.assertLessEqual(a['x']+a['w'],290)
                self.assertLessEqual(a['y']+a['h'],190)
                for b in loc[i+1:]:
                    separate=(a['x']+a['w']+5<=b['x']+0.001 or b['x']+b['w']+5<=a['x']+0.001
                              or a['y']+a['h']+5<=b['y']+0.001 or b['y']+b['h']+5<=a['y']+0.001)
                    self.assertTrue(separate,(a,b))

    def test_oversized_and_units(self):
        p=Part(id='a',filename='x',index=1,
               shape=Polygon([(0,0),(400,0),(400,400),(0,400)]),entity_refs=[],units=4)
        req={'sheetWidth':300,'sheetHeight':200,'gap':5,'margin':10,
             'targetEfficiency':80,'allowRotation':True,
             'items':[{'id':'a','quantity':1}]}
        self.assertIsNone(estimate_sheets({'a':p},req)['recommendedSheets'])
        p.units=0
        with self.assertRaises(ProcessingError): estimate_sheets({'a':p},req)

if __name__=='__main__': unittest.main()
