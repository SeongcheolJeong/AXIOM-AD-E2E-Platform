"""Streaming sensor experiments; native parameters never imply measured calibration."""
import math
from typing import Literal
from pydantic import Field, model_validator
from .base_schemas import StrictModel

class CaptureSensor(StrictModel):
    key: str = Field(pattern=r'^[A-Za-z][A-Za-z0-9_-]{0,31}$')
    kind: Literal['rgb', 'lidar', 'radar']
    translation_m: tuple[float, float, float] = (1.5, 0.0, 1.8)
    rotation_deg: tuple[float, float, float] = (0.0, 0.0, 0.0)
    rotation_order: Literal['pitch_yaw_roll_right_hand_rule'] = 'pitch_yaw_roll_right_hand_rule'
    parameters: dict[str, str] = Field(default_factory=dict)

    @model_validator(mode='after')
    def bounded(self):
        if any(not math.isfinite(v) or abs(v) > 10 for v in self.translation_m):
            raise ValueError('Mount translation must be finite and within 10 meters')
        if any(not math.isfinite(v) or abs(v) > 180 for v in self.rotation_deg):
            raise ValueError('Mount rotation must be finite and within 180 degrees')
        limits = {'rgb': {'image_size_x', 'image_size_y', 'fov', 'sensor_tick', 'enable_postprocess_effects'},
                  'lidar': {'channels', 'range', 'points_per_second', 'rotation_frequency', 'upper_fov', 'lower_fov',
                            'sensor_tick', 'noise_stddev', 'dropoff_general_rate', 'dropoff_intensity_limit', 'dropoff_zero_intensity'},
                  'radar': {'horizontal_fov', 'vertical_fov', 'range', 'points_per_second', 'sensor_tick'}}
        if set(self.parameters) - limits[self.kind]:
            raise ValueError('Unsupported native sensor parameters')
        if any(len(v) > 80 for v in self.parameters.values()):
            raise ValueError('Native parameter values exceed limits')
        values = self.native_parameters()
        for key,value in values.items():
            if key != 'enable_postprocess_effects' and not math.isfinite(float(value)):
                raise ValueError('Native sensor parameters must be finite')
        if self.kind == 'rgb':
            if int(values['image_size_x']) != 640 or int(values['image_size_y']) != 360:
                raise ValueError('Accepted profile requires 640 by 360 camera frames')
            if not 10 <= float(values['fov']) <= 120:
                raise ValueError('Camera field of view is outside the accepted profile')
            if values['enable_postprocess_effects'] not in {'true', 'false'}:
                raise ValueError('Postprocess value must be true or false')
        if self.kind == 'lidar':
            if int(values['channels']) != 16 or not 160 <= int(values['points_per_second']) <= 32000:
                raise ValueError('Accepted LiDAR profile is 16 channels and at most 32000 points/s')
            if float(values['rotation_frequency']) != 10:
                raise ValueError('LiDAR rotation must match the 10 Hz capture clock')
            if not 0.1 <= float(values['range']) <= 100 or float(values['lower_fov']) >= float(values['upper_fov']):
                raise ValueError('Invalid LiDAR range or vertical field of view')
            if any(not -89 <= float(values[k]) <= 89 for k in ('upper_fov','lower_fov')):
                raise ValueError('LiDAR vertical field of view must be within -89..89 degrees')
            for key in ('noise_stddev','dropoff_general_rate','dropoff_intensity_limit','dropoff_zero_intensity'):
                if not 0 <= float(values[key]) <= 1:
                    raise ValueError('LiDAR noise and drop parameters must be within 0..1')
            if float(values['dropoff_intensity_limit']) == 0:
                raise ValueError('LiDAR dropoff intensity limit must be positive')
        if self.kind == 'radar':
            if not 10 <= int(values['points_per_second']) <= 1500 or not 0.1 <= float(values['range']) <= 100:
                raise ValueError('Radar profile exceeds accepted bounds')
            if any(not 1 <= float(values[k]) <= 90 for k in ('horizontal_fov','vertical_fov')):
                raise ValueError('Radar field of view exceeds accepted bounds')
        if float(values['sensor_tick']) != 0:
            raise ValueError('All sensors must sample every 10 Hz world tick')
        return self

    def native_parameters(self):
        defaults = {'rgb': {'image_size_x':'640','image_size_y':'360','fov':'90','sensor_tick':'0', 'enable_postprocess_effects':'true'},
                    'lidar': {'channels':'16','range':'80','points_per_second':'32000','rotation_frequency':'10',
                              'upper_fov':'10','lower_fov':'-30','sensor_tick':'0','noise_stddev':'0',
                              'dropoff_general_rate':'0.45','dropoff_intensity_limit':'0.8','dropoff_zero_intensity':'0.4'},
                    'radar': {'horizontal_fov':'30','vertical_fov':'15','range':'80','points_per_second':'1500','sensor_tick':'0'}}
        return {**defaults[self.kind], **self.parameters}

class SensorCaptureSpec(StrictModel):
    schema_version: Literal['sensor-capture/1'] = 'sensor-capture/1'
    duration_seconds: int = Field(default=10, strict=True, ge=10, le=30)
    sample_hz: Literal[10] = 10
    spawn_index: int = Field(default=0, strict=True, ge=0, le=10000)
    vehicle_blueprint: str = Field(default='vehicle.tesla.model3', pattern=r'^vehicle\.[A-Za-z0-9_.-]{1,100}$')
    expected_map: str = Field(default='Town10HD_Opt', min_length=1, max_length=200)
    expected_server_version: str = Field(default='1ae5356', min_length=1, max_length=100)
    rig_revision: int = Field(default=1, strict=True, ge=1)
    coordinate_convention: Literal['right_handed_x_forward_y_left_z_up'] = 'right_handed_x_forward_y_left_z_up'
    sensors: list[CaptureSensor] = Field(min_length=1, max_length=4)

    @model_validator(mode='after')
    def accepted_rig(self):
        if len({s.key for s in self.sensors}) != len(self.sensors):
            raise ValueError('Sensor keys must be unique')
        if sum(s.kind == 'rgb' for s in self.sensors) > 2 or any(sum(s.kind == kind for s in self.sensors) > 1 for kind in ('lidar','radar')):
            raise ValueError('Accepted rig supports at most two RGB, one LiDAR and one radar')
        return self

def capture_template():
    return SensorCaptureSpec(sensors=[CaptureSensor(key='front',kind='rgb'),
        CaptureSensor(key='rear',kind='rgb',rotation_deg=(0,180,0)),
        CaptureSensor(key='lidar',kind='lidar'),CaptureSensor(key='radar',kind='radar')]).model_dump(mode='json')

def register_capture_routes(api, registry):
    @api.get('/sensor-captures/capabilities', tags=['sensor experiments'])
    def capabilities():
        native = registry.get('carla-streaming-capture')
        return {'schema_version':'sensor-capture-capabilities/1',
            'adapter':native.metadata().model_dump() if native else {'id':'carla-streaming-capture','status':'not_configured'},
            'supported_schema':SensorCaptureSpec.model_json_schema(), 'template':capture_template(),
            'calibration_status':'uncalibrated_engine_model',
            'unsupported':['RAW','ISP','sensor_physical_validation','depth','semantic','instance_segmentation'],
            'limits':{'duration_seconds':[10,30],'camera_count':2,'camera_size':[640,360],'sample_hz':10,
                      'lidar_count':1,'radar_count':1,'native_artifact_bytes':768*1024**2}}
