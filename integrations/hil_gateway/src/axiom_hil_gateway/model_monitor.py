"""Reference ECU measurement monitor. This module runs only in the ADAS process."""


class ModelMonitor:
    def __init__(self, policy):
        self.policy=policy;self.latched=False;self.fresh=0;self.last_bad_ms=None
        self.release_candidate=None;self.last_sequence=None
        self.members={n:ModelMonitor(p) for n,p in policy.get('members',{}).items()}

    def step(self, sample, sequence, now_ms):
        if self.members:
            qualification=self.policy.get('release_qualification')
            valid=sample is not None and sample.get('sequence')==sequence
            previous=self.latched
            fresh=True;age=None;reason='legacy_immediate';candidate=self.release_candidate
            if qualification:
                if qualification!=dict(algorithm='fresh-window-v2',period_ms=50,fresh_windows=2,max_age_ms=10):
                    raise ValueError('Unsupported release qualification contract')
                age=now_ms-sequence*50
                fresh=(valid and 0<=age<=qualification['max_age_ms'] and
                    (self.last_sequence is None or sequence==self.last_sequence+1))
            reports={n:m.step(sample.get('samples',{}).get(n) if valid else None,sequence,now_ms) for n,m in self.members.items()}
            active=[n for n,r in reports.items() if r['protect']]
            pending=False;confirmed=None
            if qualification:
                if active or not fresh:
                    self.release_candidate=None
                    pending=not active
                    reason='active_fault' if active else 'measurement_not_fresh'
                elif previous:
                    if candidate is None:
                        self.release_candidate=dict(sequence=sequence,time_ms=now_ms,required_sequence=sequence+qualification['fresh_windows'])
                        pending=True;reason='await_next_complete_window'
                    elif sequence>=candidate['required_sequence'] and sequence*50>=candidate['time_ms']:
                        self.release_candidate=None;confirmed=sequence;reason='fresh_window_confirmed'
                    else:
                        pending=True;reason='await_next_complete_window'
                else:
                    self.release_candidate=None;reason='tracking'
            self.latched=bool(active) or pending
            self.last_sequence=sequence
            guard=dict(algorithm=qualification['algorithm'] if qualification else 'immediate-v1',
                pending=pending,reason=reason,input_age_ms=age,fresh=fresh,
                candidate=self.release_candidate,confirmed_sequence=confirmed)
            return dict(boundary='multiple',sample=sample,policy=self.policy,members=reports,active_boundaries=active,
                protect=self.latched,detected=self.latched and not previous,recovered=previous and not self.latched,
                missing=any(r['missing'] for r in reports.values()) or not fresh,release_guard=guard,
                state='degraded' if any(r['state']=='degraded' for r in reports.values()) else 'recovering' if self.latched else 'tracking')
        missing=sample is None or sample.get('sequence')!=sequence
        inactive=not missing and sample['phase']=='before'
        boundary=self.policy['boundary']
        if missing:
            bad=True;basis='measurement_missing'
        elif inactive:
            bad=False;basis='monitor_not_started'
        elif boundary in ('camera_raw_csi','fpga'):
            bad=sample['abort_count']>0 or sample['last_status']=='aborted'
            basis='frame_abort' if bad else 'frame_status'
        elif boundary=='power':
            bad=sample['min_v']<self.policy['power_min_v'];basis='voltage_below_minimum'
        elif boundary=='analog':
            bad=sample['min_v']<self.policy['analog_min_v'] or sample['max_v']>self.policy['analog_max_v'];basis='voltage_out_of_range'
        else:raise ValueError('Unsupported reference measurement boundary')
        detected=bad and not self.latched;recovered=False
        if bad:self.latched=True;self.fresh=0;self.last_bad_ms=now_ms
        elif self.latched and not inactive:
            self.fresh+=1
            if self.fresh>=self.policy['recovery_samples'] and now_ms-self.last_bad_ms>=self.policy['min_hold_ms']:
                self.latched=False;recovered=True
        state='degraded' if bad else 'recovering' if self.latched else 'tracking'
        return dict(boundary=boundary,basis=basis,sample=sample,policy=self.policy,missing=missing,detected=detected,
            recovered=recovered,protect=self.latched,state=state,fresh_samples=self.fresh,last_bad_ms=self.last_bad_ms,
            hold_remaining_ms=max(0,self.policy['min_hold_ms']-(now_ms-self.last_bad_ms)) if self.latched else 0)
