/* AXIOM host driver/configuration for unmodified autoas communication modules.
 * This optional executable is GPL-3.0 under the selected upstream license.
 * IDs are sorted because upstream CanIf uses binary search. */
#include "bridge.h"
#include "Com.h"
#include "Com_Cfg.h"
#include "Com_Priv.h"
#include "PduR.h"
#undef DET_THIS_MODULE_ID
#include "PduR_Priv.h"
#include "PduR_CanIf.h"
#include "CanIf.h"
#undef DET_THIS_MODULE_ID
#include "CanIf_Priv.h"
#include "CanIf_Can.h"
#include "Can.h"
#include "Std_Critical.h"
#include <string.h>

static const unsigned ids[4] = {0x100, 0x101, 0x200, 0x201};
static uint8_t data[8][8];
static const uint8_t initial[8] = {0};
static Com_SignalConfigType signals[8];
static const Com_SignalConfigType *signal_ptrs[8][1];
static Com_IPduConfigType pdus[8];
static Com_IPduTxContextType tx_context[4];
static Com_IPduRxContextType rx_context[4];
static Com_IPduTxConfigType tx_config[4];
static Com_IPduRxConfigType rx_config[4];
static Com_GlobalContextType com_context;
const Com_ConfigType Com_Config = {pdus, signals, &com_context, 8, 8, 1};

/* Callback wrappers record actual module entry; implementations remain upstream. */
static Std_ReturnType route_can_tx(PduIdType id, const PduInfoType *info) {
  ax_trace("PduR", "Transmit", id);
  ax_trace("CanIf", "Transmit", id);
  return CanIf_Transmit(id, info);
}
static void route_com_rx(PduIdType id, const PduInfoType *info) {
  ax_trace("PduR", "RxIndication", id);
  ax_trace("Com", "RxIndication", id);
  Com_RxIndication(id, info);
}
static void route_com_confirm(PduIdType id, Std_ReturnType result) {
  ax_trace("PduR", "TxConfirmation", id);
  ax_trace("Com", "TxConfirmation", id);
  Com_TxConfirmation(id, result);
}
static const PduR_ApiType can_api = {.Transmit = route_can_tx};
static const PduR_ApiType com_api = {
  .RxIndication = route_com_rx, .TxConfirmation = route_com_confirm};
static PduR_PduType sources[8], destinations[8];
static PduR_RoutingPathType paths[8];
const PduR_ConfigType PduR_Config = {paths, 8};
static CanIf_RxPduType can_rx[4];
static CanIf_TxPduType can_tx[4];
static CanIf_CtrlContextType can_context[1];
static const CanIf_CtrlConfigType can_ctrl[1] = {{can_rx, 4}};
const CanIf_ConfigType CanIf_Config = {can_tx, can_context, can_ctrl, 4, 1};
static Can_ControllerStateType controller_state = CAN_CS_STOPPED;
static int pending_confirm = -1;

Std_ReturnType Can_Write(Can_HwHandleType hth, const Can_PduType *info) {
  if (hth != 0 || info->length != 8 || info->id > 0x7ff || pending_confirm >= 0)
    return E_NOT_OK;
  ax_trace("CanDriver", "Write", info->swPduHandle);
  ax_frame(info->id, info->sdu);
  pending_confirm = info->swPduHandle;
  return E_OK;
}
Std_ReturnType Can_SetControllerMode(uint8_t controller, Can_ControllerStateType state) {
  if (controller != 0) return E_NOT_OK;
  controller_state = state;
  return E_OK;
}
Std_ReturnType Can_GetControllerMode(uint8_t controller, Can_ControllerStateType *state) {
  if (controller != 0 || !state) return E_NOT_OK;
  *state = controller_state;
  return E_OK;
}
/* The JSONL process is single threaded. No ISR or concurrent entry exists. */
imask_t Std_EnterCritical(void) { return 0; }
void Std_ExitCritical(imask_t mask) { (void)mask; }
int std_get_log_level(void) { return 99; }
int std_get_as_log_level(const char *name, int level) { (void)name; return level; }
Std_ReturnType Det_ReportError(uint16_t module, uint8_t instance, uint8_t api, uint8_t error) {
  (void)instance; (void)api;
  ax_trace("Det", "Error", ((unsigned)module << 16) | error);
  return E_OK;
}

void ax_bsw_init(void) {
  memset(data, 0, sizeof(data));
  memset(tx_context, 0, sizeof(tx_context));
  memset(rx_context, 0, sizeof(rx_context));
  pending_confirm = -1;
  for (unsigned i = 0; i < 8; ++i) {
    signals[i] = (Com_SignalConfigType){.ptr = data[i], .initPtr = initial,
      .HandleId = i, .PduId = i, .BitPosition = 0, .BitSize = 64,
      .type = COM_UINT8N, .Endianness = COM_OPAQUE, .isGroupSignal = FALSE};
    signal_ptrs[i][0] = &signals[i];
    pdus[i] = (Com_IPduConfigType){.ptr = data[i], .signals = signal_ptrs[i],
      .GroupRefMask = 1, .length = 8, .numOfSignals = 1};
    paths[i] = (PduR_RoutingPathType){.SrcPduRef = &sources[i],
      .DestPduRef = &destinations[i], .numOfDestPdus = 1};
    if (i < 4) {
      tx_config[i] = (Com_IPduTxConfigType){.context = &tx_context[i], .TxPduId = i};
      pdus[i].txConfig = &tx_config[i];
      sources[i] = (PduR_PduType){PDUR_MODULE_COM, i, &com_api};
      destinations[i] = (PduR_PduType){PDUR_MODULE_CANIF, i, &can_api};
      can_tx[i] = (CanIf_TxPduType){.txConfirm = PduR_CanIfTxConfirmation,
        .txPduId = i, .canid = ids[i], .hoh = 0, .ControllerId = 0};
      can_rx[i] = (CanIf_RxPduType){.rxInd = PduR_CanIfRxIndication,
        .rxPduId = i + 4, .canid = ids[i], .mask = 0x7ff, .hoh = 0};
    } else {
      rx_config[i - 4] = (Com_IPduRxConfigType){.context = &rx_context[i - 4]};
      pdus[i].rxConfig = &rx_config[i - 4];
      sources[i] = (PduR_PduType){PDUR_MODULE_CANIF, i - 4, &can_api};
      destinations[i] = (PduR_PduType){PDUR_MODULE_COM, i, &com_api};
    }
  }
  CanIf_Init(NULL); ax_trace("CanIf", "Init", 0);
  PduR_Init(NULL); ax_trace("PduR", "Init", 0);
  Com_Init(NULL); ax_trace("Com", "Init", 0);
  CanIf_SetControllerMode(0, CAN_CS_STARTED);
  CanIf_SetPduMode(0, CANIF_ONLINE);
  Com_IpduGroupStart(0, TRUE);
  ax_trace("Com", "IpduGroupStart", 0);
}
int ax_bsw_tx(unsigned index, const uint8_t *bytes) {
  if (index >= 4) return E_NOT_OK;
  ax_trace("Com", "SendSignal", index);
  int result = Com_SendSignal(index, bytes);
  if (result != E_OK) return result;
  ax_trace("Com", "TriggerIPDUSend", index);
  result = Com_TriggerIPDUSend(index);
  if (pending_confirm >= 0) {
    unsigned pdu = (unsigned)pending_confirm;
    pending_confirm = -1;
    ax_trace("CanIf", "TxConfirmation", pdu);
    CanIf_TxConfirmation(pdu);
  }
  return result;
}
int ax_bsw_rx(unsigned index, const uint8_t *bytes, uint8_t *result) {
  if (index >= 4) return E_NOT_OK;
  Can_HwType mailbox = {.CanId = ids[index], .Hoh = 0, .ControllerId = 0};
  PduInfoType info = {.SduDataPtr = (uint8_t *)bytes, .SduLength = 8, .MetaDataPtr = NULL};
  ax_trace("CanIf", "RxIndication", index);
  CanIf_RxIndication(&mailbox, &info);
  return ax_bsw_read(index, result);
}
int ax_bsw_read(unsigned index, uint8_t *result) {
  if (index >= 4) return E_NOT_OK;
  ax_trace("Com", "ReceiveSignal", index + 4);
  return Com_ReceiveSignal(index + 4, result);
}
void ax_bsw_tick(void) {
  ax_trace("CanIf", "MainFunction", 0); CanIf_MainFunction();
  ax_trace("Com", "MainFunctionRx", 0); Com_MainFunctionRx();
  ax_trace("Com", "MainFunctionTx", 0); Com_MainFunctionTx();
}
