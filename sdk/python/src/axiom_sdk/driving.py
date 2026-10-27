"""Recorded driving input/receipt checks use the same UI API; no hidden dispatch."""
class DrivingPolicyClientMixin:
    def driving_policy_capabilities(self):
        return self._request('GET','/driving-policies/capabilities').json()

    def preflight_driving_input(self, manifest: dict):
        return self._request('POST','/driving-policies/preflight',json=manifest).json()

    def inspect_driving_result(self, receipt: dict):
        return self._request('POST','/driving-policies/inspect-result',json=receipt).json()
