"""Question-led gallery over retained, project-scoped CARLA evidence."""
from .experiments import _object_map_identity


class DemoCaseClientMixin:
    def demo_cases(self, *, project_id: str = 'default'):
        """List ready and unavailable curated cases; never starts or probes engines."""
        return self._request('GET', '/demo-cases', params={
            'project_id': _object_map_identity(project_id)}).json()
