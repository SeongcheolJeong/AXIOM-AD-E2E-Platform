from .retained import RetainedISPHandler

def create_handler(*,database=None):
    return RetainedISPHandler(database=database)
