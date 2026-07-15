#ifndef APP_CW_H
#define APP_CW_H

#ifdef ENABLE_FEAT_ELW_CW

#include <stdint.h>
#include <stdbool.h>
#include "driver/keyboard.h"
#include "ui/ui.h"
#include "misc.h"
#include "app/t9.h"

/* ------------------------------------------------------------------ */
/* Chat history                                                        */
/* ------------------------------------------------------------------ */

#define CW_COMPOSE_MAX      65  /* 64 chars + NUL */
#define CW_HISTORY_LINES    16
#define CW_HISTORY_WIDTH    43  /* 42 chars + NUL */
#define CW_VISIBLE_LINES     6  /* history lines shown on screen (gFont5x7 layout) */
#define CW_TEXT_COLS_FIRST  20  /* chars after prefix: (128-6)/6 */
#define CW_TEXT_COLS_CONT   21  /* chars on continuation line: 128/6 */

#define CW_SCOPE_LEN        128  /* rhythm-scope columns (one per LCD x) */
#define CW_SCOPE_DECIM        8  /* ms between scope samples -> ~1s window */

typedef enum { CW_MSG_RX = 0, CW_MSG_TX = 1, CW_MSG_CONT = 2 } CwMsgTag_t;

typedef struct {
    char       text[CW_HISTORY_WIDTH];
    CwMsgTag_t tag;
} CwHistoryEntry_t;

extern CwHistoryEntry_t cw_history[CW_HISTORY_LINES];
extern uint8_t          cw_history_count;
extern uint8_t          cw_scroll;
extern int8_t           cw_tx_recall;

extern char      cw_compose[CW_COMPOSE_MAX];
extern T9State_t cw_t9;

extern char    cw_live_char;
extern bool    cw_cursor_visible;

/* ------------------------------------------------------------------ */
/* Internal helper (also used by ui/cw.c)                             */
/* ------------------------------------------------------------------ */

void cw_history_push(const char *text, CwMsgTag_t tag);
void CW_RecallText(char *out, uint8_t max);

/* ------------------------------------------------------------------ */
/* Public API                                                          */
/* ------------------------------------------------------------------ */

void CW_Init(void);
void CW_ProcessKeys(KEY_Code_t Key, bool bKeyPressed, bool bKeyHeld);
void CW_TimeSlice10ms(void);
void CW_RX_Sample(void);   /* called from SysTick every 1ms */
void CW_TimeSlice500ms(void);

/* Phase 3 */
void CW_TX_Start(const char *text);
bool CW_TX_Active(void);

/* Phase 4 */
void     CW_RX_SetThreshold(uint16_t rssi_threshold);
uint16_t CW_RX_GetThreshold(void);
uint8_t  CW_RX_GetLastAmp(void);
uint8_t  CW_RX_GetWpm(void);                       /* detected sender WPM (0 = none) */
uint8_t  CW_RX_GetSLevel(void);                    /* peak-hold S-meter, 0..9 */
void     CW_RX_UpdateSMeter(void);                 /* call at ~10ms while on the CW screen */
uint8_t  CW_RX_GetState(void);                     /* 0 idle, 1 mark, 2 space */
void     CW_RX_GetLivePattern(char *buf, uint8_t n);
void     CW_RX_GetScope(uint8_t *buf);             /* CW_SCOPE_LEN bytes, oldest..newest */

/* Prediction popup accessors (used by ui/cw.c) */
bool        CW_PopupActive(void);
uint8_t     CW_PopupSel(void);
uint8_t     CW_PopupEffectiveCount(void);
const char *CW_PopupItemText(uint8_t display_idx);
uint8_t     CW_PopupItemCount(uint8_t display_idx);

/* Callsign prediction — reload sort after external callsign change */
void CW_PredResort(void);

#endif /* ENABLE_FEAT_ELW_CW */
#endif /* APP_CW_H */
