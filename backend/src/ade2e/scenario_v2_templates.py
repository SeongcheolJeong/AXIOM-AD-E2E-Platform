"""Authored kinematic templates; no claim of captured road reconstruction."""
from .scenario_v2_schemas import ScenarioV2

def templates():
    base={'name':'Lane follow','template':'lane_follow','actors':[{'id':'ego','role':'ego','pose':{'x':5,'y':-1.75},'speed_mps':10}],
          'observers':[{'kind':'collision'},{'kind':'minimum_distance'},{'kind':'minimum_ttc'},{'kind':'lane_departure'},
                       {'kind':'maximum_speed','threshold':15},{'kind':'maximum_acceleration','threshold':8},{'kind':'maximum_jerk','threshold':20}]}
    from copy import deepcopy
    result=[]
    for key,name in [('lane_follow','Lane follow'),('cut_in','Cut in'),('emergency_brake','Emergency brake'),
                     ('pedestrian_crossing','Pedestrian crossing'),('merge','Merge'),('lead_stop','Lead stop')]:
        value=deepcopy(base);value.update(template=key,name=name)
        if key in ('cut_in','merge'):
            value['actors'].append({'id':'other','pose':{'x':22 if key=='cut_in' else 14,'y':-5.25},'speed_mps':8,
                'actions':[{'kind':'lane_change','target_lane':-1,'transition_s':2,'trigger':{'value':2}}]})
        elif key in ('emergency_brake','lead_stop'):
            value['actors'].append({'id':'lead','pose':{'x':35,'y':-1.75},'speed_mps':8,
                'actions':[{'kind':'stop','transition_s':1.5,'trigger':{'value':2}}]})
            if key=='emergency_brake':value['actors'][0]['actions']=[{'kind':'stop','transition_s':1.5,'trigger':{'value':3}}]
        elif key=='pedestrian_crossing':
            value['actors'].append({'id':'pedestrian','kind':'pedestrian','pose':{'x':35,'y':-7,'heading_rad':1.57079632679},
                'dimensions':{'length_m':.5,'width_m':.5,'height_m':1.75},'actions':[{'kind':'follow_path','trigger':{'value':1},
                 'path':[{'x':35,'y':-7,'heading_rad':1.57079632679,'time_s':1},{'x':35,'y':1,'heading_rad':1.57079632679,'time_s':5}]}]})
        result.append({'id':key,'fidelity':'authored_object_kinematics','scenario':ScenarioV2.model_validate(value).model_dump(mode='json')})
    traffic=deepcopy(base)
    traffic.update(name='Traffic interaction: lane change, stop and crossing',template='traffic_interaction',duration_s=10,
      actors=[
        {'id':'ego','role':'ego','pose':{'x':5,'y':-1.75},'speed_mps':8},
        {'id':'lane_changer','pose':{'x':30,'y':-5.25},'speed_mps':8,
         'actions':[{'kind':'lane_change','target_lane':-1,'transition_s':2,'trigger':{'value':1}}]},
        {'id':'lead','pose':{'x':65,'y':-1.75},'speed_mps':6,
         'actions':[{'kind':'stop','transition_s':1.5,'trigger':{'value':4}}]},
        {'id':'pedestrian','kind':'pedestrian','pose':{'x':85,'y':-7,'heading_rad':1.57079632679},
         'dimensions':{'length_m':.5,'width_m':.5,'height_m':1.75},
         'actions':[{'kind':'follow_path','trigger':{'value':5},'path':[
           {'x':85,'y':-7,'heading_rad':1.57079632679,'time_s':5},
           {'x':85,'y':1,'heading_rad':1.57079632679,'time_s':8}]}]},
        {'id':'trailing','pose':{'x':5,'y':-5.25},'speed_mps':6},
      ])
    result.append({'id':'traffic_interaction','fidelity':'authored_object_kinematics',
                   'scenario':ScenarioV2.model_validate(traffic).model_dump(mode='json')})
    return result
