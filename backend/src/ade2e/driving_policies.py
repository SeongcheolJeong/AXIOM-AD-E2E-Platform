"""Shared UI/SDK inspection API; no unconfigured native dispatch or safety verdict."""
from fastapi import HTTPException

from .driving_contract import SOURCE_COMMIT, PROFILE, content_hash, preflight, validate_result


def register_driving_routes(api):
    @api.get('/driving-policies/capabilities')
    def capabilities():
        return {'schema_version':'driving-capabilities/1','profile':PROFILE,'source_commit':SOURCE_COMMIT,
            'native_execution_status':'not_configured','research_only':True,
            'supported':['causal_input_preflight','native_receipt_inspection','trajectory_mode_visualization'],
            'unsupported':['server_native_dispatch','detection_ab_criteria','closed_loop_safety','commercial_data_distribution']}

    @api.post('/driving-policies/preflight')
    def input_check(body:dict):
        try:
            return preflight(body)
        except (ValueError,TypeError,KeyError) as error:
            raise HTTPException(422,str(error)) from error

    @api.post('/driving-policies/inspect-result')
    def result_check(body:dict):
        try:
            validate_result(body)
            return {'schema_version':'driving-inspection/1','receipt_hash':content_hash(body),
                'input_hash':body['input_hash'],'profile':PROFILE,'frames':len(body['outputs']),
                'integrity_status':'consistent_receipt','execution_claim':body['execution']['status'],
                'origin_verification':'self_reported_receipt_not_independently_verified',
                'verification_verdict':'not_evaluated','outputs':body['outputs'],'model':body['model'],
                'execution':body['execution'],'limitations':body['limitations']}
        except (ValueError,TypeError,KeyError) as error:
            raise HTTPException(422,str(error)) from error
