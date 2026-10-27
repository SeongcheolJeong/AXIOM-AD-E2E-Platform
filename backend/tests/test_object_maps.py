"""Map authoring and compilation contracts; no simulator or physical validation."""
import copy
import hashlib
import math
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch
from xml.etree import ElementTree as ET
from fastapi import APIRouter, Depends, FastAPI, Request
from fastapi.testclient import TestClient
from ade2e.database import Database, Settings
from ade2e.object_maps import register_object_map_routes, compile_source, compiler_info
from ade2e.object_maps_schemas import ObjectMapDefinition
from ade2e.object_maps_geometry import advance, preview_map
from ade2e.object_maps_import import import_opendrive
from ade2e.simulation_catalog import register_simulation_catalog_routes
from ade2e.simulation_catalog_models import SimulationCatalogRevisionRow
from ade2e.simulation_catalog_schemas import Rights
from ade2e.team_access import Principal

RIGHTS={'license':'authored-test-only','redistribution':'restricted'}

def straight():
    return {'name':'Authored road','roads':[{'id':'main','origin':{'x':0,'y':0,'z':0,'heading_rad':0},'geometries':[{'kind':'line','length_m':80}],'lanes':[{'id':-1},{'id':-2},{'id':1}]}],'rights':RIGHTS}


def junction():
    return {'name':'Explicit crossing','roads':[{'id':identity,'origin':{'x':x,'y':0,'z':0,'heading_rad':0},'geometries':[{'length_m':20}],'junction_id':'cross' if identity=='connector' else None} for identity,x in [('approach',0),('connector',20),('exit',40)]],'connections':[{'from_road':'approach','from_lane':-1,'to_road':'connector','to_lane':-1},{'from_road':'connector','from_lane':-1,'to_road':'exit','to_lane':-1}],'junctions':[{'id':'cross'}],'rights':RIGHTS}


class ObjectMapGeometryTests(unittest.TestCase):
    def test_same_direction_adjacency_and_positive_lane_travel(self):
        result=preview_map(straight());nodes={n['id']:n for n in result['nodes']}
        a=nodes['r1:l-1:p0'];b=nodes['r1:l-2:p0'];opposite=nodes['r1:l1:p0']
        self.assertEqual(a['right_id'],b['id']);self.assertNotIn('left_id',a)
        self.assertTrue(a['lane_change_right']);self.assertGreater(a['y'],0);self.assertLess(opposite['y'],0)
        self.assertEqual(opposite['next_ids'],[]);self.assertEqual(nodes['r1:l1:p1']['next_ids'],[opposite['id']])
        self.assertAlmostEqual(abs(opposite['heading_rad']),math.pi)

    def test_clothoid_integrates_heading_and_straight_limit(self):
        x,y,h=advance(0,0,0,20,0,.02)
        self.assertAlmostEqual(h,.2);self.assertGreater(x,19.8);self.assertGreater(y,1.3)
        self.assertEqual(advance(0,0,0,10,0,0),(10,0,0))

    def test_curvature_and_contiguous_lanes_reject_impossible_geometry(self):
        spec=straight();spec['roads'][0]['geometries']=[{'kind':'arc','length_m':20,'curvature_start':.2,'curvature_end':.2}]
        self.assertFalse(preview_map(spec)['buildable'])
        spec=straight();spec['roads'][0]['lanes']=[{'id':-2}]
        with self.assertRaises(ValueError):preview_map(spec)

    def test_disconnected_connections_do_not_silently_snap(self):
        spec=junction();spec['roads'][1]['origin']['x']+=5
        result=preview_map(spec);self.assertFalse(result['buildable'])
        self.assertIn('disconnected_lane',[v['code'] for v in result['issues']])

    def test_junction_group_reaches_approach_and_closed_lane_is_retained(self):
        spec=junction();spec['roads'][1]['lanes']=[{'id':-1,'closed':True,'priority':7}]
        result=preview_map(spec);nodes={n['id']:n for n in result['nodes']}
        self.assertEqual(nodes['r1:l-1:p10']['next_ids'],['r2:l-1:p0'])
        self.assertEqual(nodes['r1:l-1:p10']['conflict_ids'],['junction:cross'])
        self.assertTrue(nodes['r2:l-1:p0']['closed']);self.assertEqual(nodes['r2:l-1:p0']['priority'],7)

    def test_native_objects_block_build_instead_of_claiming_obstacles(self):
        spec=straight();spec['objects']=[{'id':'box','road_id':'main','s_m':10}]
        result=preview_map(spec);self.assertFalse(result['buildable'])
        self.assertIn('native_object_unsupported',[v['code'] for v in result['issues']])

    def test_over_complex_source_rejected_before_sampling(self):
        spec=straight();spec['roads'][0]['geometries']=[{'length_m':2000}]*32
        with self.assertRaisesRegex(ValueError,'50,000'):preview_map(spec)


class ObjectMapApiTests(unittest.TestCase):
    def setUp(self):
        self.directory=tempfile.TemporaryDirectory();self.db=Database(Settings(data_dir=Path(self.directory.name),seed_examples=False));self.db.initialize()
        self.principal=Principal('legacy')
        def auth(request:Request):request.state.principal=self.principal
        router=APIRouter(prefix='/api/v1',dependencies=[Depends(auth)])
        register_object_map_routes(router,self.db);register_simulation_catalog_routes(router,self.db)
        app=FastAPI();app.include_router(router);self.client=TestClient(app)

    def tearDown(self):self.db.engine.dispose();self.directory.cleanup()

    def save(self,definition=None,expected=0):
        response=self.client.post('/api/v1/object-maps',json={'id':'road-test','expected_revision':expected,'definition':definition or straight()})
        self.assertEqual(response.status_code,201,response.text);return response.json()

    def build(self,source,expected=0):
        return self.client.post('/api/v1/object-maps/road-test/build',json={'revision':source['revision'],'sha256':source['sha256'],'expected_map_revision':expected,'server_version':'1ae5356'})

    def test_source_immutable_retry_and_stale_revision(self):
        one=self.save();again=self.save();self.assertEqual(one['sha256'],again['sha256'])
        changed=straight();changed['roads'][0]['origin']['y']=20;two=self.save(changed,1)
        self.assertEqual(two['revision'],2);self.assertNotEqual(one['sha256'],two['sha256'])
        read=self.client.get('/api/v1/object-maps/road-test?revision=1');self.assertEqual(read.json()['sha256'],one['sha256'])
        stale=self.client.post('/api/v1/object-maps',json={'id':'road-test','expected_revision':0,'definition':changed});self.assertEqual(stale.status_code,409)

    def test_viewer_can_preview_import_but_not_save_or_build(self):
        source=self.save();self.principal=Principal('project','default','Viewer')
        self.assertEqual(self.client.post('/api/v1/object-maps/preview',json={'definition':straight()}).status_code,200)
        self.assertEqual(self.client.post('/api/v1/object-maps',json={'id':'x','definition':straight()}).status_code,403)
        self.assertEqual(self.build(source).status_code,403)
        self.assertEqual(self.client.get('/api/v1/object-maps?project_id=other').status_code,404)

    def test_build_pin_and_missing_optional_compiler_fail_closed(self):
        source=self.save();wrong={**source,'sha256':'0'*64}
        self.assertEqual(self.build(wrong).status_code,409)
        with patch('ade2e.object_maps.compiler_info',return_value={'available':False}):self.assertEqual(self.build(source).status_code,503)
        self.assertEqual(self.client.get('/api/v1/maps').json()['items'],[])

    @unittest.skipUnless(compiler_info()['available'],'optional pinned compiler not installed')
    def test_compiled_catalog_provenance_idempotence_and_actual_lane_topology(self):
        source=self.save(junction());response=self.build(source);self.assertEqual(response.status_code,201,response.text)
        result=response.json();definition=result['map']['definition']
        self.assertEqual(result['execution_status'],'not_executed')
        self.assertEqual(definition['generated']['source'],{k:source[k] for k in ('id','revision','sha256')})
        xml=ET.fromstring(definition['opendrive']['content']);self.assertEqual(xml.find('./road/link/successor').get('elementType'),'junction')
        self.assertEqual(xml.find('./junction/connection/laneLink').attrib,{'from':'-1','to':'-1'})
        retry=self.build(source);self.assertEqual(retry.status_code,201,retry.text);self.assertEqual(result['map_ref'],retry.json()['map_ref'])
        opened=self.client.get('/api/v1/maps/'+result['map_ref']['id']);self.assertEqual(opened.status_code,200)
        self.assertEqual(opened.json()['definition']['generated']['build_hash'],definition['generated']['build_hash'])

    @unittest.skipUnless(compiler_info()['available'],'optional pinned compiler not installed')
    def test_signal_stop_nodes_compile_and_source_roundtrip(self):
        spec=straight();spec['signals']=[{'id':'stop','road_id':'main','s_m':40,'kind':'stop','lane_ids':[-1]}]
        source=self.save(spec);response=self.build(source);self.assertEqual(response.status_code,201,response.text)
        native=response.json()['map']['definition'];self.assertEqual(native['signals'][0]['node_ids'],['r1:l-1:p20'])
        imported=self.client.post('/api/v1/object-maps/import',json={'content':native['opendrive']['content'],'name':'Roundtrip','rights':RIGHTS})
        self.assertEqual(imported.status_code,200,imported.text);self.assertTrue(imported.json()['editable'])
        self.assertEqual(imported.json()['definition']['roads'],source['definition']['roads'])
        self.assertEqual(imported.json()['definition']['signals'],source['definition']['signals'])

    @unittest.skipUnless(compiler_info()['available'],'optional pinned compiler not installed')
    def test_manual_xml_edit_cannot_hide_under_embedded_source(self):
        source=self.save();response=self.build(source);xml=response.json()['map']['definition']['opendrive']['content']
        edited=xml.replace('length="80.0"','length="85.0"',1)
        result=import_opendrive(edited,'Tampered',Rights(**RIGHTS))
        self.assertTrue(result.read_only);self.assertEqual(result.original_opendrive.content,edited)
        self.assertIn('differs',result.unsupported[0])

    def test_unknown_geometry_preserves_exact_original_with_reasons_and_export(self):
        xml='<?xml version="1.0"?>\n<OpenDRIVE><header name="external"/><road id="8"><surface><CRG file="private.crg"/></surface></road></OpenDRIVE>'
        result=self.client.post('/api/v1/object-maps/import',json={'content':xml,'rights':RIGHTS}).json()
        self.assertFalse(result['editable']);self.assertEqual(result['definition']['original_opendrive']['content'],xml)
        source=self.save(result['definition']);self.assertEqual(self.build(source).status_code,422)
        exported=self.client.get('/api/v1/object-maps/road-test/export?format=source_opendrive');self.assertEqual(exported.text,xml)
        self.assertEqual(exported.headers['X-Content-SHA256'],hashlib.sha256(xml.encode()).hexdigest())

    @unittest.skipUnless(compiler_info()['available'],'optional pinned compiler not installed')
    def test_generic_supported_roads_import_and_custom_marking_is_preserved_readonly(self):
        source=self.save();built=self.build(source).json();xml=ET.fromstring(built['map']['definition']['opendrive']['content'])
        for metadata in xml.findall('userData'):xml.remove(metadata)
        value=ET.tostring(xml,encoding='unicode');result=import_opendrive(value,'External editable',Rights(**RIGHTS))
        self.assertFalse(result.read_only);self.assertEqual(len(result.roads[0].lanes),3)
        xml.find('.//right/lane/roadMark').set('color','yellow')
        value=ET.tostring(xml,encoding='unicode');result=import_opendrive(value,'External yellow',Rights(**RIGHTS))
        self.assertTrue(result.read_only);self.assertEqual(result.original_opendrive.content,value)

    @unittest.skipUnless(compiler_info()['available'],'optional pinned compiler not installed')
    def test_sparse_signal_validity_and_compiler_source_hashes(self):
        spec=straight();spec['signals']=[{'id':'give-way','road_id':'main','s_m':20,'kind':'yield','lane_ids':[-2,1]}]
        source=self.save(spec);result=self.build(source);self.assertEqual(result.status_code,201,result.text)
        value=result.json();definition=value['map']['definition'];xml=ET.fromstring(definition['opendrive']['content'])
        self.assertEqual([v.attrib for v in xml.findall('.//signal/validity')],[{'fromLane':'-2','toLane':'-2'},{'fromLane':'1','toLane':'1'}])
        self.assertEqual(definition['signals'][0]['regulatory_kind'],'yield')
        self.assertEqual(len(value['build_manifest']['compiler_sha256']),64)
        self.assertEqual(len(value['build_manifest']['geometry_sha256']),64)
        self.assertEqual(definition['generated']['compiler_sha256'],value['build_manifest']['compiler_sha256'])

    @unittest.skipUnless(compiler_info()['available'],'optional pinned compiler not installed')
    def test_loaded_compiler_source_mismatch_blocks_build(self):
        source=self.save()
        with patch('axiom_opendrive.LOADED_SOURCE_SHA256','0'*64):
            self.assertEqual(self.build(source).status_code,503)

    @unittest.skipUnless(compiler_info()['available'],'optional pinned compiler not installed')
    def test_unicode_names_export_ascii_xml_character_references_losslessly(self):
        spec=straight();spec['name']='Object Sim · 교차로';spec['roads'][0]['name']='진입로 · α'
        source=self.save(spec);result=self.build(source);self.assertEqual(result.status_code,201,result.text)
        xml=result.json()['map']['definition']['opendrive']['content']
        self.assertTrue(xml.isascii());self.assertIn('&#183;',xml)
        parsed=ET.fromstring(xml);self.assertEqual(parsed.find('header').get('name'),spec['name'])
        self.assertEqual(parsed.find('road').get('name'),spec['roads'][0]['name'])
        restored=import_opendrive(xml,spec['name'],Rights(**RIGHTS))
        self.assertFalse(restored.read_only);self.assertEqual(restored.name,spec['name'])
        self.assertEqual(restored.roads[0].name,spec['roads'][0]['name'])

    def test_dtd_entities_rejected(self):
        response=self.client.post('/api/v1/object-maps/import',json={'content':'<!DOCTYPE OpenDRIVE [<!ENTITY x SYSTEM "file:///etc/passwd">]><OpenDRIVE>&x;</OpenDRIVE>','rights':RIGHTS})
        self.assertEqual(response.status_code,422)

    def test_corrupted_source_is_not_read_or_built(self):
        self.save()
        with self.db.session() as session:
            row=session.get(SimulationCatalogRevisionRow,('default','object-map-source','road-test',1));row.manifest=b'{}';session.commit()
        self.assertEqual(self.client.get('/api/v1/object-maps/road-test').status_code,409)

    def test_unsafe_graph_source_saves_draft_but_build_rejects(self):
        spec=junction();spec['roads'][1]['origin']['y']=10;source=self.save(spec)
        self.assertEqual(self.build(source).status_code,422)
        self.assertFalse(self.client.get('/api/v1/object-maps/road-test').json()['preview']['buildable'])
