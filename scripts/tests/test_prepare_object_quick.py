"""Pinned copy orchestration fixtures; no native engine or external project writes."""
import copy
import hashlib
import json
from pathlib import Path
import sys
from types import SimpleNamespace
from urllib.parse import parse_qs,urlsplit
import pytest

sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
import prepare_object_quick as quick


def catalog(kind,identity,definition,project='source'):
    value={'id':identity,'revision':1,'project_id':project,'definition':definition,'kind':kind}
    value['sha256']=quick.digest(value)
    return value


@pytest.fixture
def prepared_copy(tmp_path):
    asset=catalog('asset','prius',{'blueprint_id':'vehicle.toyota.prius'})
    authored=catalog('object-map-source','roads',{'roads':['fixture'],'rights':{'license':'fixture'}})
    common={'opendrive':{'content':'fixture','sha256':'f'*64},'nodes':[],'signals':[],'spawn_points':[],
        'rights':{'license':'fixture'},'server_version':'fixture-version'}
    generated=catalog('map','generated-map',{**common,'native_name':'generated-source','generated':{
        'source':quick.ref(authored),'compiler_id':'compiler','compiler_version':'1','compiler_sha256':'a'*64,'road_ids':{'r':1}}})
    native=catalog('map','city-map',{**common,'native_name':'TownFixture','generated':None})
    originals={'assets':{'prius':asset},'object-maps':{'roads':authored},'maps':{'generated-map':generated,'city-map':native}}
    scenarios={};selection={'source_api':'http://source/api/v1','source_project_id':'source','presets':{}}
    for index,preset in enumerate(quick.PRESETS):
        scenario={'name':preset,'seed':42,'map':quick.ref(native if preset=='urban_city' else generated),
            'actors':[{'id':'ego','asset':quick.ref(asset),'controller':{'kind':'reactive'},'behavior':{'actions':[]}}],
            'sensors':[{'id':'front','width':640,'height':360}]}
        saved={'id':'scenario_'+str(index),'project_id':'source','revision':1,'scenario':scenario,
            'configuration_hash':quick.digest(scenario),'manifest_hash':str(index)*64}
        scenarios[saved['id']]=saved
        selection['presets'][preset]={'scenario_ref':quick.scenario_ref(saved),'actor_roles':{'ego':'ego'},'source_binding_revision':3}
    class Source:
        def request(self,path,body=None,key=None):
            assert body is None,'Source must remain read-only'
            parts=urlsplit(path).path.strip('/').split('/')
            if parts[0]=='scenario-v2':return copy.deepcopy(scenarios[parts[1]])
            return copy.deepcopy(originals[parts[0]][parts[1]])
    class Target:
        def __init__(self):
            self.calls=[];self.writes=[];self.scenarios={};self.keys={};self.drift=False
            self.catalogs={'assets':{'beta-prius':catalog('asset','beta-prius',asset['definition'],'target')},
                'object-maps':{},'maps':{'city-map':catalog('map','city-map',{**native['definition'],'nodes':['different']},'target')}}
            self.presets={name:{'id':name,'binding_revision':None,'scenario_ref':None} for name in quick.PRESETS}
            self.presets['lead_stop_start']={'id':'lead_stop_start','binding_revision':5,'scenario_ref':{'id':'keep'},'saved_execution_id':'owned-baseline'}
        def items(self,kind,project):
            assert project=='target'
            return copy.deepcopy(list(self.catalogs[kind].values()))
        def request(self,path,body=None,key=None):
            self.calls.append((path,body,key));parts=urlsplit(path).path.strip('/').split('/')
            assert not path.startswith('/experiments'),'Preparation cannot submit any engine work'
            if body is not None:self.writes.append((path,body,key))
            if path.startswith('/object-sim/presets?'):return {'items':copy.deepcopy(list(self.presets.values()))}
            if body is None:
                if parts[0]=='scenario-v2':return copy.deepcopy(self.scenarios[parts[1]])
                return copy.deepcopy(self.catalogs[parts[0]][parts[1]])
            assert body['project_id']=='target'
            if len(parts)==1 and parts[0] in self.catalogs:
                assert body['expected_revision']==0 and body['id'] not in self.catalogs[parts[0]]
                if parts[0]=='maps':assert not body['definition'].get('generated')
                value=catalog(parts[0],body['id'],body['definition'],'target');self.catalogs[parts[0]][value['id']]=value
                return copy.deepcopy(value)
            if parts[0]=='object-maps' and parts[-1]=='build':
                source=self.catalogs['object-maps'][parts[1]]
                identity='authored-'+source['id'][:36]+'-'+hashlib.sha256(source['id'].encode()).hexdigest()[:8]
                definition=copy.deepcopy(generated['definition']);definition['native_name']='generated-target'
                definition['generated']['source']=quick.ref(source)
                if self.drift:definition['nodes']=['changed geometry']
                value=catalog('map',identity,definition,'target');self.catalogs['maps'][identity]=value
                return {'map':copy.deepcopy(value),'map_ref':quick.ref(value)}
            if path=='/scenario-v2':
                if key in self.keys:return copy.deepcopy(self.keys[key])
                value={'id':'target_scenario_'+str(len(self.scenarios)),'project_id':'target','revision':1,
                    'scenario':body['scenario'],'configuration_hash':quick.digest(body['scenario']),'manifest_hash':'d'*64}
                self.scenarios[value['id']]=value;self.keys[key]=value;return copy.deepcopy(value)
            if parts[0]=='object-sim' and parts[-1]=='bind':
                assert body['expected_revision']==0 and 'saved_execution_id' not in body and 'saved_comparison_execution_id' not in body
                self.presets[parts[-2]].update(binding_revision=1,scenario_ref=body['scenario_ref'])
                return {'revision':1}
            raise AssertionError(path)
    selection_path=tmp_path/'selection.json';selection_path.write_text(json.dumps(selection))
    args=SimpleNamespace(source_api=selection['source_api'],api='http://target/api/v1',project='target',
        selection=selection_path,output=tmp_path/'prepared.json')
    return args,Source(),Target(),selection,scenarios


def test_prepares_exact_inputs_in_existing_project_preserves_beta_and_is_idempotent(prepared_copy):
    args,source,target,selection,_=prepared_copy;before=copy.deepcopy(target.presets['lead_stop_start'])
    result=quick.prepare(args,source,target)
    assert result['complete'] and not result['native_execution_submitted']
    assert target.presets['lead_stop_start']==before
    receipt=json.loads(args.output.read_text());assert set(receipt['presets'])==set(quick.PRESETS)
    assert any(value['operation']=='reused_exact_definition' for value in receipt['catalogs'].values())
    assert any(value['operation']=='registered_namespaced' for value in receipt['catalogs'].values())
    assert any(value['operation']=='rebuilt_from_exact_authored_source' for value in receipt['catalogs'].values())
    assert target.catalogs['maps']['city-map']['definition']['nodes']==['different']
    for name,prepared in receipt['presets'].items():
        assert prepared['source_scenario_ref']==selection['presets'][name]['scenario_ref']
        saved=target.scenarios[prepared['scenario_ref']['id']]
        assert saved['scenario']['sensors']==[{'id':'front','width':640,'height':360}]
        assert saved['scenario']['actors'][0]['asset']['id']=='beta-prius'
    count=len(target.writes);assert quick.prepare(args,source,target)==result
    assert len(target.writes)==count


def test_verifies_all_source_pins_before_any_target_mutation(prepared_copy):
    args,source,target,_,scenarios=prepared_copy
    scenarios['scenario_3']['scenario']['seed']=99
    with pytest.raises(ValueError,match='exact source pin'):quick.prepare(args,source,target)
    assert not target.writes and not args.output.exists()


def test_existing_quick_binding_is_preserved_without_writes(prepared_copy):
    args,source,target,_,_=prepared_copy;target.presets['urban_city']['binding_revision']=2
    with pytest.raises(ValueError,match='already bound'):quick.prepare(args,source,target)
    assert not target.writes and not args.output.exists()


def test_rebuilt_generated_map_cannot_silently_change_native_geometry(prepared_copy):
    args,source,target,_,_=prepared_copy;target.drift=True
    with pytest.raises(ValueError,match='changed original geometry'):quick.prepare(args,source,target)
    assert not target.scenarios
    assert not any(path.endswith('/bind') for path,_,_ in target.writes)
