"""Metadata access to the configured optional CameraE2E adapter, without optics imports."""
from fastapi import HTTPException,Request
from .team_access import project_authorize

def register_camera_bridge_routes(api,db,registry):
    @api.get('/camera-bridge/scene-catalog',tags=['sensor experiments'])
    def scene_catalog(request:Request,project_id:str='default'):
        project_authorize(request,db,project_id,'Viewer')
        if project_id!='default':
            raise HTTPException(422,'Camera scene catalog is default-project only; cross-project source ownership is not configured')
        handler=registry.get('camera-e2e-lens-sensor-scene')
        if handler is None or handler.metadata().status!='available':
            raise HTTPException(422,'CameraE2E lens/sensor scene API is not configured')
        return {'schema_version':'camera-scene-catalog/1','adapter':handler.metadata().model_dump(),
            'source_scope':'default_project_only','catalog':handler.sources()}

    @api.get('/camera-bridge/capabilities',tags=['sensor experiments'])
    def capabilities():
        handler=registry.get('camera-e2e-retained-isp')
        scene_handler=registry.get('camera-e2e-lens-sensor-scene')
        return {'schema_version':'camera-bridge/1','capability_scope':'retained_raw_isp_adapter','adapter':handler.metadata().model_dump() if handler else
                {'id':'camera-e2e-retained-isp','status':'not_configured'},
            'comparison_kind':'same_raw_isp_ab','source_scope':'default_project_only',
            'physical_validation':'not_established','unsupported':['detector_accuracy','same_scene_lens_sensor_ab','inverse_raw_reconstruction'],
            'lens_sensor_scene':{'adapter':scene_handler.metadata().model_dump() if scene_handler else
                {'id':'camera-e2e-lens-sensor-scene','status':'not_configured'},
                'comparison_kind':'same_scene_sensor_ab','source_scope':'default_project_only',
                'scene_scope':'central_authored_slanted_edge_model_diagnostic',
                'physical_validation':'not_established','road_label_correspondence':'not_established'}}

    @api.get('/camera-bridge/sources',tags=['sensor experiments'])
    def sources(request:Request):
        project_authorize(request,db,'default','Viewer')
        handler=registry.get('camera-e2e-retained-isp')
        if handler is None or handler.metadata().status!='available':raise HTTPException(422,'CameraE2E API is not configured')
        return {'items':handler.sources()}
