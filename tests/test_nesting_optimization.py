import unittest
from shapely.geometry import box
from engine import Part
from estimate import pack_order,optimize_rectangles,estimate_sheets

class NestingOptimizationTests(unittest.TestCase):
    def test_multiple_strategies_reduce_sheet_count_without_overlap(self):
        dims=[(320,320),(400,240),(240,400),(480,360),(480,360),(560,520),(640,440),(600,240),(360,560),(160,240),(600,480),(520,520),(280,520),(520,320)]
        parts=[Part(str(i),'test.dxf',i,box(0,0,w,h),[]) for i,(w,h) in enumerate(dims)]
        ordered=sorted(parts,key=lambda p:(-p.shape.area,-max(p.shape.bounds[2:])))
        baseline=pack_order(ordered,1000,1000,10,5,True,'short')
        events=[]
        best,count=optimize_rectangles(parts,1000,1000,10,5,True,lambda n,total:events.append((n,total)))
        self.assertEqual((len(baseline),len(best)),(4,3))
        self.assertEqual(count,16)
        self.assertEqual(events,list((n,16) for n in range(1,17)))
        self.assertEqual(sum(len(s.placements) for s in best),len(parts))
        for s in best:
            for i,a in enumerate(s.placements):
                self.assertGreaterEqual(a['x'],10);self.assertGreaterEqual(a['y'],10)
                self.assertLessEqual(a['x']+a['w'],990.001);self.assertLessEqual(a['y']+a['h'],990.001)
                for b in s.placements[i+1:]:
                    self.assertTrue(a['x']+a['w']+5<=b['x']+.002 or b['x']+b['w']+5<=a['x']+.002 or a['y']+a['h']+5<=b['y']+.002 or b['y']+b['h']+5<=a['y']+.002)
    def test_edge_margin_does_not_require_an_extra_gap(self):
        p=Part('a','test.dxf',1,box(0,0,980,980),[])
        req={'sheetWidth':1000,'sheetHeight':1000,'margin':10,'gap':5,'items':[{'id':'a','quantity':1}]}
        result=estimate_sheets({'a':p},req)
        self.assertEqual(result['packingSheets'],1)
        self.assertEqual(result['recommendedSheets'],1)
        self.assertEqual(result['oversized'],[])
    def test_target_does_not_inflate_actual_packing(self):
        p=Part('a','test.dxf',1,box(0,0,400,400),[])
        req={'sheetWidth':1000,'sheetHeight':1000,'margin':10,'gap':5,'targetEfficiency':50,'items':[{'id':'a','quantity':4}]}
        result=estimate_sheets({'a':p},req)
        self.assertEqual(result['packingSheets'],1)
        self.assertEqual(result['recommendedSheets'],1)
        self.assertEqual(result['theoreticalSheets'],2)
