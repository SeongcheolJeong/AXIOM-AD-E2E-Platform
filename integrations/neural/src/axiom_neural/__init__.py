"""Core-facing connector; neural engine imports remain in the native runner."""

def create_handler(database=None):
    from .handler import NeuralExperimentHandler
    return NeuralExperimentHandler(database=database)
