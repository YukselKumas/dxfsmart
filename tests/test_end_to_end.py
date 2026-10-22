import io
import zipfile
import ezdxf
from fastapi.testclient import TestClient
from shapely.geometry import Polygon
from app import app
from demo import sample_files
from engine import Part, analyze_dxf
from estimate import estimate_sheets

def test_demo_analysis_export_and_sheet_plan():
    client=TestClient(app)
    response=client.get('/api/demo')
    assert response.status_code==200
    d=response.json()
    assert d['summary']['files']==2
    assert d['summary']['parts']==5
    assert d['summary']['duplicateGroups']>=1
    assert any(i['code']=='SELF_INTERSECT' for i in d['issues'])
    assert any(i['code']=='OPEN' for i in d['issues'])
    assert any(p['holes']==1 for p in d['parts'])
    job=d['jobId']
    plan=client.post('/api/estimate/'+job,json={'sheetWidth':1500,'sheetHeight':3000,'gap':5,'margin':10,
       'targetEfficiency':80,'allowRotation':True,'items':[{'id':p['id'],'quantity':50} for p in d['parts']]})
    assert plan.status_code==200
    r=plan.json()
    assert r['totalQuantity']==250 and r['recommendedSheets']>=1
    assert r['packingSheets']==len(r['sheets'])
    for s in r['sheets']:
        items=s['placements']
        for i,p in enumerate(items):
            assert p['x']>=10 and p['y']>=10
            assert p['x']+p['w']<=1490.01 and p['y']+p['h']<=2990.01
            for b in items[i+1:]:
                assert p['x']+p['w']+5<=b['x']+1e-6 or b['x']+b['w']+5<=p['x']+1e-6 or p['y']+p['h']+5<=b['y']+1e-6 or b['y']+b['h']+5<=p['y']+1e-6
    zipresponse=client.get('/api/download/'+job)
    assert zipresponse.status_code==200
    with zipfile.ZipFile(io.BytesIO(zipresponse.content)) as z:
        dxfs=[n for n in z.namelist() if n.endswith('.dxf')]
        assert len(dxfs)==5
        assert {'Parca_Listesi.csv','ONCE_OKUYUN.txt'} <= set(z.namelist())
        from pathlib import Path
        import tempfile
        with tempfile.TemporaryDirectory() as tmp:
            for fname in dxfs:
                path=Path(tmp)/fname;path.write_bytes(z.read(fname))
                doc=ezdxf.readfile(path)
                assert len(doc.modelspace())>=1
                assert doc.header.get('$INSUNITS')==4

def test_pack_reject_nonmillimeter_and_nonfitting():
    p=Part(id='p',filename='x.dxf',index=1,shape=Polygon([(0,0),(2000,0),(2000,4000),(0,4000)]),entity_refs=[],units=4)
    q={'items':[{'id':'p','quantity':1}], 'sheetWidth':1500,'sheetHeight':3000,'margin':10}
    a=estimate_sheets({'p':p},q)
    assert a['recommendedSheets'] is None and len(a['oversized'])==1
    p.units=0
    try:
        estimate_sheets({'p':p},q)
        assert False
    except Exception as e:
        assert 'mm' in str(e)

def test_open_or_invalid_not_exported_as_valid_parts():
    f1,f2=sample_files()
    r=analyze_dxf(f2[1],f2[0],.05)
    assert len(r.parts)==2 # 80x120 with hole, and a standalone circle; bow-tie excluded
    assert any(i['code']=='SELF_INTERSECT' for i in r.issues)
