"""Log Sim investigation and bounded native variants use the same UI API."""
import math
from .collections import _key
from .perception import _identity


class LogSimClientMixin:
    def log_sim_sources(self, *, project_id='default'):
        return self._request('GET','/log-sim/sources',params={'project_id':_identity(project_id)}).json()

    def log_sim_source(self, source_id, *, project_id='default'):
        return self._request('GET',f'/log-sim/sources/{_identity(source_id)}',params={'project_id':_identity(project_id)}).json()

    def log_sim_events(self, source_id, *, project_id='default', q='', kind=None,
                       start_time_s=None, end_time_s=None, offset=0, limit=50):
        """Search deterministic recorded-state review candidates, not safety verdicts."""
        if not isinstance(q, str) or len(q) > 120:
            raise ValueError('Event query must be a string of at most 120 characters')
        if kind not in (None, 'low_speed', 'deceleration', 'nearby_ahead'):
            raise ValueError('Unsupported Log Sim event kind')
        if type(offset) is not int or not 0 <= offset <= 10000 or type(limit) is not int or not 1 <= limit <= 100:
            raise ValueError('Use offset 0..10000 and limit 1..100')
        for value in (start_time_s, end_time_s):
            if value is not None and (type(value) not in (int, float) or not math.isfinite(value) or value < 0):
                raise ValueError('Event time bounds must be finite nonnegative seconds')
        if start_time_s is not None and end_time_s is not None and start_time_s > end_time_s:
            raise ValueError('Use an ordered event time interval')
        params = {'project_id': _identity(project_id), 'q': q, 'offset': offset, 'limit': limit}
        params.update({key: value for key, value in {'kind': kind, 'start_time_s': start_time_s,
            'end_time_s': end_time_s}.items() if value is not None})
        return self._request('GET', f'/log-sim/sources/{_identity(source_id)}/events', params=params).json()

    def create_log_sim_case(self, source_id, *, title, note, start_time_s, end_time_s, idempotency_key, project_id='default'):
        if any(type(v) not in (float,int) or not math.isfinite(v) for v in (start_time_s,end_time_s)) or not 0<=start_time_s<=end_time_s:
            raise ValueError('Use a finite ordered source interval')
        return self._request('POST',f'/log-sim/sources/{_identity(source_id)}/cases',json={
            'project_id':_identity(project_id),'title':title,'note':note,'start_time_s':start_time_s,'end_time_s':end_time_s},headers=_key(idempotency_key)).json()

    def log_sim_case(self, source_id, case_id, *, project_id='default'):
        return self._request('GET',f'/log-sim/sources/{_identity(source_id)}/cases/{_identity(case_id)}',
            params={'project_id':_identity(project_id)}).json()

    def preview_log_sim_variant(self, source_id, *, lead_wait_s, project_id='default', case_id=None, case_revision=None):
        body=self._log_sim_variant_body(source_id,lead_wait_s,project_id,case_id,case_revision)
        return self._request('POST','/log-sim/variants/preview',json=body).json()

    def submit_log_sim_variant(self, source_id, *, lead_wait_s, preview_hash, idempotency_key, project_id='default', case_id=None, case_revision=None):
        body=self._log_sim_variant_body(source_id,lead_wait_s,project_id,case_id,case_revision)
        if not isinstance(preview_hash,str) or len(preview_hash)!=64 or any(c not in '0123456789abcdef' for c in preview_hash):
            raise ValueError('Use the exact preview SHA-256')
        return self._request('POST','/log-sim/variants',json=body|{'preview_hash':preview_hash},headers=_key(idempotency_key)).json()

    def log_sim_variants(self, *, project_id='default', source_id=None):
        params={'project_id':_identity(project_id)}
        if source_id is not None:params['source_id']=_identity(source_id)
        return self._request('GET','/log-sim/variants',params=params).json()

    def log_sim_replay_profiles(self, *, project_id='default'):
        return self._request('GET', '/log-sim/replay-profiles', params={'project_id': _identity(project_id)}).json()

    def preview_log_sim_comparison(self, source_id, *, case_id, case_revision, project_id='default',
                                    baseline_profile='balanced', candidate_profile='cautious'):
        body = self._log_sim_comparison_body(source_id, case_id, case_revision, project_id, baseline_profile, candidate_profile)
        return self._request('POST', '/log-sim/comparisons/preview', json=body).json()

    def submit_log_sim_comparison(self, source_id, *, case_id, case_revision, preview_hash, idempotency_key,
                                   project_id='default', baseline_profile='balanced', candidate_profile='cautious'):
        body = self._log_sim_comparison_body(source_id, case_id, case_revision, project_id, baseline_profile, candidate_profile)
        if not isinstance(preview_hash, str) or len(preview_hash) != 64 or any(c not in '0123456789abcdef' for c in preview_hash):
            raise ValueError('Use the exact comparison preview SHA-256')
        return self._request('POST', '/log-sim/comparisons', json=body | {'preview_hash': preview_hash},
            headers=_key(idempotency_key)).json()

    def log_sim_comparison(self, comparison_id, *, project_id='default'):
        return self._request('GET', f'/log-sim/comparisons/{_identity(comparison_id)}',
            params={'project_id': _identity(project_id)}).json()

    def log_sim_comparisons(self, *, project_id='default', source_id=None):
        params = {'project_id': _identity(project_id)}
        if source_id is not None:
            params['source_id'] = _identity(source_id)
        return self._request('GET', '/log-sim/comparisons', params=params).json()

    def save_log_sim_regression(self, comparison_id, *, name, idempotency_key, project_id='default',
                                 max_deceleration_request_mps2=5., max_target_speed_mps=10.):
        if not isinstance(name, str) or not 1 <= len(name) <= 180:
            raise ValueError('Regression name must contain 1..180 characters')
        for value, maximum in ((max_deceleration_request_mps2, 15), (max_target_speed_mps, 50)):
            if type(value) not in (int, float) or not math.isfinite(value) or not 0 <= value <= maximum:
                raise ValueError('Use finite regression limits: deceleration 0..15 m/s² and target speed 0..50 m/s')
        return self._request('POST', f'/log-sim/comparisons/{_identity(comparison_id)}/regressions',
            json={'project_id': _identity(project_id), 'name': name,
                  'max_deceleration_request_mps2': max_deceleration_request_mps2, 'max_target_speed_mps': max_target_speed_mps},
            headers=_key(idempotency_key)).json()

    def run_log_sim_regression(self, regression_id, *, idempotency_key, project_id='default'):
        return self._request('POST', f'/log-sim/regressions/{_identity(regression_id)}/runs',
            json={'project_id': _identity(project_id)}, headers=_key(idempotency_key)).json()

    def log_sim_regressions(self, *, project_id='default', source_id=None):
        params = {'project_id': _identity(project_id)}
        if source_id is not None:
            params['source_id'] = _identity(source_id)
        return self._request('GET', '/log-sim/regressions', params=params).json()

    @staticmethod
    def _log_sim_comparison_body(source_id, case_id, case_revision, project_id, baseline_profile, candidate_profile):
        if type(case_revision) is not int or case_revision < 1:
            raise ValueError('Pin a positive Case revision')
        profiles = ('balanced', 'cautious', 'responsive')
        if baseline_profile not in profiles or candidate_profile not in profiles:
            raise ValueError('Use a supported reference-controller profile')
        return {'project_id': _identity(project_id), 'source_id': _identity(source_id),
            'case_id': _identity(case_id), 'case_revision': case_revision,
            'baseline_profile': baseline_profile, 'candidate_profile': candidate_profile}

    @staticmethod
    def _log_sim_variant_body(source_id,lead_wait_s,project_id,case_id,case_revision):
        if type(lead_wait_s) not in (int,float) or not math.isfinite(lead_wait_s) or not 0<=lead_wait_s<=6:
            raise ValueError('Lead wait must be a finite number from 0 to 6 seconds')
        if (case_id is None)!=(case_revision is None) or case_revision is not None and (type(case_revision) is not int or case_revision<1):
            raise ValueError('Pin both Case identity and positive revision')
        return {'project_id':_identity(project_id),'source_id':_identity(source_id),'lead_wait_s':lead_wait_s,
            'case_id':_identity(case_id) if case_id is not None else None,'case_revision':case_revision}
