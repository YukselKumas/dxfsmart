import unittest,io,zipfile
from shapely.geometry import Polygon
from engine import Part,ProcessingError,part_filename
from planning import estimate_job,planned_parts

class PlanningTests(unittest.TestCase):
    def setUp(self):
        self.parts={str(i):Part(id=str(i),filename='çizim.dxf',index=i,shape=Polygon([(0,0),(1000,0),(1000,1000),(0,1000)]),entity_refs=[],thickness_mm=t,quantity=q)
                    for i,t,q in [(1,2,3),(2,5,2)]}
        self.req={'sheetWidth':1500,'sheetHeight':3000,'gap':5,'margin':10,'items':[{'id':'1','quantity':3},{'id':'2','quantity':2}]}
    def test_quantities_mass_and_separate_thickness_plans(self):
        result=estimate_job(self.parts,self.req)
        self.assertAlmostEqual(result['totalNetKg'],125.6,3)
        self.assertEqual(result['totalQuantity'],5)
        self.assertEqual(len(result['groups']),2)
        self.assertEqual(result['totalSheets'],sum(g['recommendedSheets'] for g in result['groups']))
        for group in result['groups']:
            self.assertTrue(all(self.parts[p['id']].thickness_mm==group['thicknessMm'] for s in group['sheets'] for p in s['placements']))
    def test_unit_confirmation_only_for_unitless(self):
        self.parts['1'].units=0
        with self.assertRaises(ProcessingError): estimate_job(self.parts,self.req)
        result=estimate_job(self.parts,{**self.req,'confirmMillimeters':True})
        self.assertEqual(result['totalQuantity'],5)
        self.assertEqual(self.parts['1'].units,0)
        self.parts['1'].units=1
        with self.assertRaises(ProcessingError): estimate_job(self.parts,{**self.req,'confirmMillimeters':True})
    def test_density_unknown_thickness_and_export_quantity(self):
        self.parts['1'].thickness_mm=None
        with self.assertRaises(ProcessingError): estimate_job(self.parts,self.req)
        req={**self.req,'defaultThickness':2,'material':'aluminum'}
        result=estimate_job(self.parts,req)
        self.assertAlmostEqual(result['totalNetKg'],43.2,3)
        planned=planned_parts(self.parts,{**req,'items':[{'id':'1','quantity':20}]})
        self.assertTrue(part_filename(planned[0]).endswith('_2mm_20adet.dxf'))
        self.assertEqual(self.parts['1'].quantity,3)
    def test_invalid_and_zero_quantities(self):
        for qty in [-1,True,2.5,1201]:
            with self.assertRaises(ProcessingError): planned_parts(self.parts,{**self.req,'items':[{'id':'1','quantity':qty}]})
        result=estimate_job(self.parts,{**self.req,'items':[{'id':'1','quantity':0}]})
        self.assertEqual(result['totalNetKg'],0)
        self.assertEqual(result['totalSheets'],0)
