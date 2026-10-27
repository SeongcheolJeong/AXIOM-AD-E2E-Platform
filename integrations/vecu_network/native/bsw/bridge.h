#ifndef AXIOM_BSW_BRIDGE_H
#define AXIOM_BSW_BRIDGE_H
#include <stdint.h>
#ifdef __cplusplus
extern "C" {
#endif
void ax_trace(const char *module, const char *event, unsigned id);
void ax_frame(unsigned can_id, const uint8_t *data);
void ax_bsw_init(void);
int ax_bsw_tx(unsigned index, const uint8_t *data);
int ax_bsw_rx(unsigned index, const uint8_t *data, uint8_t *result);
void ax_bsw_tick(void);
int ax_bsw_read(unsigned index, uint8_t *result);
#ifdef __cplusplus
}
#endif
#endif
