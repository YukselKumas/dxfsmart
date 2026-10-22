import unittest
from shapely.geometry import Polygon
from engine import Part,ProcessingError
from planning import estimate_job

class CuttingTimeTests(unittest.TestCase):
    def test_length_piercing_quantity_and_overhead(self):
        shape=Polygon([(0,0),(100,0),(100,100),(0,100)],[[(20,20),(30,20),(30,30),(20,30)]])
        p=Part('a','x.dxf',1,shape,[],thickness_mm=2)
        req={'sheetWidth':1500,'sheetHeight':3000,'items':[{'id':'a','quantity':10}],
             'cuttingSpeed':1000,'pierceSeconds':3,'overheadPercent':20}
        result=estimate_job({'a':p},req)
        self.assertEqual(result['cutLengthM'],4.4)
        self.assertEqual(result['pierceCount'],20)
        self.assertAlmostEqual(result['estimatedMinutes'],6.48,2)
        faster=estimate_job({'a':p},{**req,'cuttingSpeeds':{'2':2000}})
        self.assertAlmostEqual(faster['estimatedMinutes'],3.84,2)
        self.assertEqual(faster['totalNetKg'],result['totalNetKg'])
        self.assertEqual(faster['totalSheets'],result['totalSheets'])
    def test_invalid_speeds(self):
        p=Part('a','x.dxf',1,Polygon([(0,0),(100,0),(100,100),(0,100)]),[],thickness_mm=2)
        req={'sheetWidth':1500,'sheetHeight':3000,'items':[{'id':'a','quantity':1}]}
        for patch in ({'cuttingSpeed':0},{'pierceSeconds':-1},{'overheadPercent':float('nan')},{'cuttingSpeeds':{'2':0}}):
            with self.assertRaises(ProcessingError): estimate_job({'a':p},{**req,**patch})
