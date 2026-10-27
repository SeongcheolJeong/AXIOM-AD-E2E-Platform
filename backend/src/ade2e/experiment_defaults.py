"""One handler inventory shared by the API and worker; optional engines stay optional."""
from importlib.util import find_spec
from importlib import import_module
from .experiment_runtime import ExperimentRegistry
from .object_sim import EsminiExperimentHandler
from .planning_replay import PlanningReplayHandler
from .neural_perception import NeuralPerceptionHandler

def default_experiment_registry(db):
    registry=ExperimentRegistry(database=db)
    for handler in (EsminiExperimentHandler(database=db),PlanningReplayHandler(db),NeuralPerceptionHandler(db)):
        if registry.get(handler.metadata().id) is None:registry.register(handler)
    # Source/editable optional packages can expose their factory before package
    # entry-point metadata has been refreshed. No simulator library is imported.
    for module,name in (('axiom_carla.multisensor','create_capture_handler'),
                        ('axiom_carla.feedback','create_feedback_handler'),
                        ('axiom_carla.driving_multisensor','create_driving_multisensor_handler'),
                        ('axiom_carla.city','create_city_handler'),
                        ('axiom_carla.map_qualification','create_map_qualification_handler'),
                        ('axiom_carla.hil','create_hil_handler'),
                        ('axiom_carla.stunt','create_stunt_handler'),
                        ('axiom_neural','create_handler'),
                        ('axiom_camera_e2e','create_handler'),
                        ('axiom_camera_e2e.lens_scene','create_lens_scene_handler'),('axiom_vad.experiment','NativeVADExperimentHandler')):
        try:available=find_spec(module) is not None
        except ModuleNotFoundError:available=False
        if available:
            handler=getattr(import_module(module),name)(database=db)
            if registry.get(handler.metadata().id) is None:registry.register(handler)
    return registry
