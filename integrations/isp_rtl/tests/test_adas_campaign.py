"""Campaign contract tests with explicit fixtures, separate from native evidence."""
import json
from pathlib import Path
import pytest
from axiom_isp_rtl import campaign


def config(**changes):
    return dict(engine='adas_validation_campaign',mode='extended_smoke',long_frames=30,
                seeds=[42,7],reset_count=2,stall_cycles=[5000,8000],include_preboard=False,**changes)


def fixture_native(c,folder,check,identity,seed):
    """Deterministic manifest fixture, not a simulator and never native evidence."""
    folder=Path(folder);folder.mkdir(parents=True,exist_ok=True)
    progress=json.loads((folder.parents[1]/'campaign-progress.json').read_text())
    assert progress['current_case']==folder.name
    assert all(set(row)=={'id','status','verdict'} for row in progress['cases'])
    period=c['raster_width']*c['raster_height']*100000000/c['pixel_rate_hz']
    aborted={}
    for fault in c['faults']:
        if fault['kind']=='output_stall' and fault['duration_cycles']<7000:continue
        frame=int((fault['at_cycle']-256)/period)
        aborted[fault['channel'],frame]={'output_stall':'fifo_overflow','reset':'channel_reset','input_gap':'frame_boundary'}[fault['kind']]
    frames=[];receivers=[]
    for ch in range(4):
        for n in range(c['frames']):
            loss=aborted.get((ch,n),'');pixels=3 if loss else (c['width']-4)*(c['height']-4)
            row=dict(channel=ch,frame=n,status='aborted' if loss else 'committed',
                     input_sha256=f'{seed%4096:064x}',output_sha256='b'*64,reference_sha256='b'*64,
                     config_version=0,epoch=0,received_pixels=pixels,terminal_ns=n*100+99,loss_reason=loss)
            frames.append(row)
            receivers.append(dict(channel=ch,frame=n,status='discarded' if loss else 'published',
                visible=not loss,received_pixels=pixels,discarded_pixels=pixels if loss else 0,
                late_discarded_pixels=0,published_at_ns=0 if loss else row['terminal_ns'],
                epoch=0,config_version=0,published_sha256=None if loss else row['output_sha256']))
    receiver=dict(schema_version='adas-transactional-consumer/1',frames=receivers,
                  published_frames=len(frames)-len(aborted),discarded_pixels=len(aborted)*3,
                  late_discarded_pixels=0,protocol_error_count=0,aborted_visible_frames=0)
    (folder/'consumer.json').write_text(json.dumps(receiver))
    summary=dict(design_hash='f'*64,verdict='passed',completed_frames=len(frames)-len(aborted),aborted_frames=len(aborted),
                 committed_mismatch_count=0,unexpected_mismatch_count=0,protocol_error_count=0,missing_frames=0,
                 source_window_ns=round(c['frames']*period*10),
                 **{'consumer_'+k:v for k,v in receiver.items() if k not in ('schema_version','frames')})
    return dict(summary=summary,frames=dict(frames=frames,channels=[dict(channel=ch,fifo_peak_pixels=4096 if any(k[0]==ch for k in aborted) else 3000) for ch in range(4)]))


def test_disabled_preboard_is_incomplete_but_configured_cases_can_pass(monkeypatch,tmp_path):
    monkeypatch.setattr(campaign.adas,'run',fixture_native)
    result=campaign.run(config(),tmp_path,lambda:None,{},42)['summary']
    assert result['execution_verdict']=='passed' and result['verdict']=='incomplete'
    assert result['gates']['multi_seed']=='passed'
    assert result['source_seed_diversity']['unique_inputs']==2
    assert all(x['trace_artifact']=='cases/'+x['id']+'/waveform.vcd' for x in result['cases'])
    assert result['gates']['fifo_threshold']=='passed'
    assert result['gates']['preboard_checks']=='not_run'
    assert result['gates']['full1080p_continuous']=='not_evaluated'
    assert result['readiness']=='incomplete' and not result['board_ready']
    progress=json.loads((tmp_path/'campaign-progress.json').read_text())
    assert progress['final'] and progress['current_case'] is None
    assert progress['completed']==progress['total']==11
    assert (tmp_path/'campaign-progress.json').stat().st_size<3000
    assert not (tmp_path/'campaign-progress.json.tmp').exists()


def test_single_seed_and_missing_threshold_cannot_be_passed(monkeypatch,tmp_path):
    monkeypatch.setattr(campaign.adas,'run',fixture_native)
    c=config();c.update(seeds=[42],stall_cycles=[100,200])
    result=campaign.run(c,tmp_path,lambda:None,{},42)['summary']
    assert result['execution_verdict']=='passed' and result['verdict']=='incomplete'
    assert result['gates']['multi_seed']==result['gates']['fifo_threshold']=='incomplete'
    assert len(result['incomplete_reasons'])==3


@pytest.mark.parametrize('mutation',['missing_summary','missing_artifact','visible_abort','different_hash','wrong_total'])
def test_missing_or_inconsistent_consumer_evidence_fails_closed(monkeypatch,tmp_path,mutation):
    def broken(c,folder,check,identity,seed):
        result=fixture_native(c,folder,check,identity,seed)
        path=folder/'consumer.json'
        if mutation=='missing_summary':result['summary'].pop('consumer_aborted_visible_frames')
        elif mutation=='missing_artifact':path.unlink()
        else:
            data=json.loads(path.read_text())
            if mutation=='visible_abort':
                rows=[r for r in data['frames'] if r['status']=='discarded']
                if rows:rows[0]['visible']=True
            elif mutation=='different_hash':data['frames'][0]['published_sha256']='c'*64
            else:data['published_frames']+=1
            path.write_text(json.dumps(data))
        return result
    monkeypatch.setattr(campaign.adas,'run',broken)
    result=campaign.run(config(),tmp_path,lambda:None,{},42)['summary']
    assert result['verdict']==result['execution_verdict']=='failed'
    assert result['errors']


def test_modulo_colliding_seeds_are_incomplete_even_when_all_children_pass(monkeypatch,tmp_path):
    monkeypatch.setattr(campaign.adas,'run',fixture_native)
    c=config();c['seeds']=[42,4138]
    result=campaign.run(c,tmp_path,lambda:None,{},42)['summary']
    assert result['execution_verdict']=='passed' and result['verdict']=='incomplete'
    assert result['gates']['multi_seed']=='incomplete'
    assert result['source_seed_diversity']['unique_inputs']==1
    assert result['source_seed_diversity']['groups'][0]['seeds']==[42,4138]
    assert any('duplicate RAW' in x for x in result['incomplete_reasons'])
