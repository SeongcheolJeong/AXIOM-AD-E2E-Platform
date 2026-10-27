"""Bounded reference ECU hooks. No hardware firmware, voltage or OS emulation claim."""
KINDS = {'ecu_reset', 'os_event_drop'}


class ReferenceHooks:
    def __init__(self, faults):
        self.faults = [f for f in faults if f['kind'] in KINDS]
        self.reset = False
        self.os_blocked = False
        self.epoch = 0

    def advance(self, time_ms):
        reset = any(f['kind'] == 'ecu_reset' and f['start_ms'] <= time_ms < min(f['end_ms'],f['start_ms']+f.get('reset_restart_ms',60000)) for f in self.faults)
        blocked = any(f['kind'] == 'os_event_drop' and f['start_ms'] <= time_ms < f['end_ms'] for f in self.faults)
        events = []
        if reset != self.reset:
            if reset:
                self.epoch += 1
            events.append(('reset_asserted' if reset else 'reset_released', {'epoch': self.epoch}))
        if blocked != self.os_blocked:
            events.append(('os_event_blocked' if blocked else 'os_event_restored', {'event': 'adas_control_tick'}))
        self.reset, self.os_blocked = reset, blocked
        return events


def validate_runtime_faults(runtime):
    allowed = {'udp_delay', 'udp_drop', 'udp_repeat', 'restbus_drop', 'can_command_drop', 'brake_feedback_drop'}
    if runtime['device']['target'] == 'vcan':
        allowed |= KINDS
        if runtime.get('model_input'):
            allowed |= {'model_'+k for k in ('camera_raw_csi','fpga','power','analog')}
    elif runtime.get('model_input'):
        raise ValueError('Model measurement replay requires reference ECU; physical driver is not registered')
    for f in runtime['faults']:
        if f['kind'] not in allowed:
            raise ValueError('Fault requires a registered physical driver or reference hook: ' + f['kind'])
        if not 0 < f['start_ms'] < f['end_ms'] <= 60000:
            raise ValueError('Fault window outside runtime bounds')
