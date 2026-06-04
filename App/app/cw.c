#ifdef ENABLE_FEAT_ELW_CW

#include <string.h>
#include "app/cw.h"
#include "app/t9.h"
#include "settings.h"
#include "driver/bk4819.h"
#include "driver/bk4819-regs.h"
#include "driver/systick.h"
#include "radio.h"
#include "app/generic.h"
#include "functions.h"
#include "misc.h"
#include "app/menu.h"
#include "ui/menu.h"
#include "ui/ui.h"

/* ------------------------------------------------------------------ */
/* Morse table                                                         */
/* ------------------------------------------------------------------ */

typedef struct { uint8_t len; uint16_t code; } MorseChar_t;

/* bits [len-1:0], MSB = first element; 0=dit 1=dah */
static const MorseChar_t morse_table[36] = {
    {2, 0b01},    /* A .-   */
    {4, 0b1000},  /* B -... */
    {4, 0b1010},  /* C -.-. */
    {3, 0b100},   /* D -..  */
    {1, 0b0},     /* E .    */
    {4, 0b0010},  /* F ..-. */
    {3, 0b110},   /* G --.  */
    {4, 0b0000},  /* H .... */
    {2, 0b00},    /* I ..   */
    {4, 0b0111},  /* J .--- */
    {3, 0b101},   /* K -.-  */
    {4, 0b0100},  /* L .-.. */
    {2, 0b11},    /* M --   */
    {2, 0b10},    /* N -.   */
    {3, 0b111},   /* O ---  */
    {4, 0b0110},  /* P .--. */
    {4, 0b1101},  /* Q --.- */
    {3, 0b010},   /* R .-.  */
    {3, 0b000},   /* S ...  */
    {1, 0b1},     /* T -    */
    {3, 0b001},   /* U ..-  */
    {4, 0b0001},  /* V ...- */
    {3, 0b011},   /* W .--  */
    {4, 0b1001},  /* X -..- */
    {4, 0b1011},  /* Y -.-- */
    {4, 0b1100},  /* Z --.. */
    {5, 0b11111}, /* 0 ----- */
    {5, 0b01111}, /* 1 .---- */
    {5, 0b00111}, /* 2 ..--- */
    {5, 0b00011}, /* 3 ...-- */
    {5, 0b00001}, /* 4 ....- */
    {5, 0b00000}, /* 5 ..... */
    {5, 0b10000}, /* 6 -.... */
    {5, 0b11000}, /* 7 --... */
    {5, 0b11100}, /* 8 ---.. */
    {5, 0b11110}, /* 9 ----. */
};

static const MorseChar_t *cw_lookup(char c)
{
    if (c >= 'a' && c <= 'z') return &morse_table[c - 'a'];
    if (c >= 'A' && c <= 'Z') return &morse_table[c - 'A'];
    if (c >= '0' && c <= '9') return &morse_table[26 + (c - '0')];
    return NULL;
}

/* ------------------------------------------------------------------ */
/* TX state machine                                                    */
/* ------------------------------------------------------------------ */

typedef enum {
    CW_TX_IDLE,
    CW_TX_ARMING,       /* waiting for FUNCTION_TRANSMIT to become active */
    CW_TX_ELEMENT_ON,
    CW_TX_ELEMENT_OFF,
    CW_TX_CHAR_GAP,
    CW_TX_WORD_GAP,
    CW_TX_DONE,
} CwTxState_t;

static CwTxState_t  tx_state     = CW_TX_IDLE;
static char         tx_buf[CW_COMPOSE_MAX];
static const char  *tx_ptr       = NULL;
static uint8_t      tx_elem_idx  = 0;
static uint8_t      tx_elem_len  = 0;
static uint16_t     tx_elem_code = 0;
static uint16_t     tx_tick      = 0;

static uint16_t dit_ticks;

/* ------------------------------------------------------------------ */
/* Prediction popup                                                    */
/* ------------------------------------------------------------------ */

/* Index 0-13: static items. Index 14: callsign (text from gEeprom.CW_CALLSIGN) */
static const char * const cw_pred_text[CW_PRED_COUNT] = {
    "CQ", "DE", "K", "73", "KN", "AR", "SK",
    "QSL", "QRZ?", "BK", "TNX", "FB", "QTH", "HI",
    NULL    /* placeholder — callsign text comes from gEeprom.CW_CALLSIGN */
};
#define CW_CALLSIGN_IDX  (CW_PRED_COUNT - 1u)  /* = 14 */

static bool    cw_popup_active = false;
static uint8_t cw_popup_sel   = 0;
static uint8_t cw_pred_order[CW_PRED_COUNT];
static uint8_t cw_pred_count[CW_PRED_COUNT];

/* effective count: 14 static items always, plus callsign if set */
static uint8_t cw_pred_effective(void)
{
    return (gEeprom.CW_CALLSIGN[0] != '\0') ? (uint8_t)CW_PRED_COUNT
                                             : (uint8_t)(CW_PRED_COUNT - 1u);
}

static void cw_pred_sort(void)
{
    uint8_t n = cw_pred_effective();
    for (uint8_t i = 0; i < n; i++) cw_pred_order[i] = i;
    for (uint8_t i = 1; i < n; i++) {
        uint8_t key = cw_pred_order[i];
        int8_t  j   = (int8_t)i - 1;
        while (j >= 0 && cw_pred_count[cw_pred_order[(uint8_t)j]] < cw_pred_count[key]) {
            cw_pred_order[(uint8_t)(j + 1)] = cw_pred_order[(uint8_t)j];
            j--;
        }
        cw_pred_order[(uint8_t)(j + 1)] = key;
    }
}

static void cw_pred_load(void)
{
    for (uint8_t i = 0; i < CW_PRED_COUNT; i++)
        cw_pred_count[i] = gEeprom.CW_PRED_COUNTS[i];
    cw_pred_sort();
}

static void cw_pred_save(void)
{
    for (uint8_t i = 0; i < CW_PRED_COUNT; i++)
        gEeprom.CW_PRED_COUNTS[i] = cw_pred_count[i];
    SETTINGS_SaveCwPredCounts();
}

static void cw_pred_insert(uint8_t item_idx)
{
    const char *text = (item_idx == CW_CALLSIGN_IDX)
                       ? gEeprom.CW_CALLSIGN
                       : cw_pred_text[item_idx];
    if (!text || !text[0]) return;

    uint8_t tlen = (uint8_t)strlen(text);
    uint8_t clen = (uint8_t)strlen(cw_compose);

    T9_Commit(&cw_t9);  /* finalise any pending T9 character first */

    if (clen > 0 && (clen + 1u + tlen) < (CW_COMPOSE_MAX - 1u)) {
        cw_compose[clen] = ' ';
        memcpy(cw_compose + clen + 1, text, tlen + 1);
        cw_t9.len = clen + 1 + tlen;
    } else if (clen == 0 && tlen < (CW_COMPOSE_MAX - 1u)) {
        memcpy(cw_compose, text, tlen + 1);
        cw_t9.len = tlen;
    }

    if (cw_pred_count[item_idx] < 255u) cw_pred_count[item_idx]++;
    cw_pred_sort();
    cw_pred_save();

    /* keep selection on the same item after re-sort */
    uint8_t n = cw_pred_effective();
    for (uint8_t i = 0; i < n; i++) {
        if (cw_pred_order[i] == item_idx) { cw_popup_sel = i; break; }
    }
}

/* accessors for ui/cw.c */
bool        CW_PopupActive(void)                   { return cw_popup_active; }
uint8_t     CW_PopupSel(void)                      { return cw_popup_sel; }
uint8_t     CW_PopupEffectiveCount(void)           { return cw_pred_effective(); }
const char *CW_PopupItemText(uint8_t display_idx)  {
    uint8_t idx = cw_pred_order[display_idx];
    return (idx == CW_CALLSIGN_IDX) ? gEeprom.CW_CALLSIGN : cw_pred_text[idx];
}
uint8_t     CW_PopupItemCount(uint8_t display_idx) { return cw_pred_count[cw_pred_order[display_idx]]; }

/* ------------------------------------------------------------------ */
/* Callsign entry screen                                               */
/* ------------------------------------------------------------------ */

/* Called by menu after saving callsign so the popup re-sorts */
void CW_PredResort(void) { cw_pred_sort(); }
static uint16_t dah_ticks;
static uint16_t cgap_ticks;
static uint16_t wgap_ticks;

static void cw_tx_load_next_char(void)
{
    while (*tx_ptr == ' ' || *tx_ptr == '\0') {
        if (*tx_ptr == '\0') {
            tx_state = CW_TX_DONE;
            return;
        }
        tx_state = CW_TX_WORD_GAP;
        tx_tick  = wgap_ticks;
        tx_ptr++;
        return;
    }
    const MorseChar_t *m = cw_lookup(*tx_ptr++);
    if (m == NULL) { cw_tx_load_next_char(); return; }
    tx_elem_len  = m->len;
    tx_elem_code = m->code;
    tx_elem_idx  = 0;
    tx_state     = CW_TX_ELEMENT_ON;
    tx_tick      = 0;
}

static void cw_tx_tick(void)
{
    if (tx_state == CW_TX_IDLE) return;

    /* PTT released mid-message — abort cleanly.
       Skip this check while ARMING: TX isn't active yet, that's expected. */
    if (tx_state != CW_TX_ARMING && gCurrentFunction != FUNCTION_TRANSMIT) {
        if (gCurrentVfo != NULL && gCurrentVfo->Modulation == MODULATION_CW)
            BK4819_CW_KeyUp();
        else
            BK4819_ExitDTMF_TX(false);
        tx_state = CW_TX_IDLE;
        gRequestDisplayScreen = DISPLAY_CW_CHAT;
        return;
    }

    if (tx_tick > 0) { tx_tick--; return; }

    const bool ook = (gCurrentVfo != NULL && gCurrentVfo->Modulation == MODULATION_CW);

    switch (tx_state) {

    case CW_TX_ARMING:
        /* Wait here until FUNCTION_TRANSMIT becomes active */
        if (gCurrentFunction != FUNCTION_TRANSMIT) break;
        if (ook) {
            /* OOK CW: FUNCTION_Transmit already armed the PA with unmodulated carrier.
               PA starts muted (KeyUp) — first ELEMENT_ON will key it down. */
            BK4819_CW_KeyUp();
        } else {
            /* AF CW: set up tone generator in TX mute mode */
            BK4819_EnterTxMute();
            BK4819_WriteRegister(BK4819_REG_70,
                BK4819_REG_70_MASK_ENABLE_TONE1 | (66u << BK4819_REG_70_SHIFT_TONE1_TUNING_GAIN));
            BK4819_WriteRegister(BK4819_REG_71,
                (uint16_t)(((uint32_t)gEeprom.CW_TONE_HZ * 1353245u + (1u << 16)) >> 17));
            BK4819_SetAF(BK4819_AF_MUTE);
            BK4819_EnableTXLink();
        }
        tx_tick = 5;    /* 50ms settle before first element */
        tx_state = CW_TX_ELEMENT_ON;
        cw_tx_load_next_char();
        break;

    case CW_TX_ELEMENT_ON: {
        bool is_dah = (tx_elem_code >> (tx_elem_len - 1 - tx_elem_idx)) & 1;
        if (ook)
            BK4819_CW_KeyDown();
        else
            BK4819_ExitTxMute();
        tx_tick  = is_dah ? dah_ticks : dit_ticks;
        tx_state = CW_TX_ELEMENT_OFF;
        break;
    }

    case CW_TX_ELEMENT_OFF:
        if (ook)
            BK4819_CW_KeyUp();
        else
            BK4819_EnterTxMute();
        tx_elem_idx++;
        if (tx_elem_idx >= tx_elem_len) {
            tx_state = CW_TX_CHAR_GAP;
            tx_tick  = cgap_ticks;
        } else {
            tx_tick  = dit_ticks;
            tx_state = CW_TX_ELEMENT_ON;
        }
        break;

    case CW_TX_CHAR_GAP:
    case CW_TX_WORD_GAP:
        cw_tx_load_next_char();
        break;

    case CW_TX_DONE:
        /* Message finished — stop carrier then release PTT programmatically */
        if (ook)
            BK4819_CW_KeyUp();
        else
            BK4819_ExitDTMF_TX(false);
        tx_state = CW_TX_IDLE;
        GENERIC_Key_PTT(false);   /* triggers APP_HandleEndTransmission → RADIO_SendEndOfTransmission */
        gPttIsPressed = false;    /* prevent polling loop from restarting TX if button still held */
        gRequestDisplayScreen = DISPLAY_CW_CHAT;
        break;

    default:
        break;
    }
}

/* forward declaration — defined in the History section below */
static void cw_push_raw(const char *text, CwMsgTag_t tag);

/* ------------------------------------------------------------------ */
/* RX state machine                                                    */
/* ------------------------------------------------------------------ */

typedef enum {
    CW_RX_IDLE,
    CW_RX_MARK,
    CW_RX_SPACE,
} CwRxState_t;

static CwRxState_t rx_state       = CW_RX_IDLE;
static uint16_t    rx_mark_ticks  = 0;
static uint16_t    rx_space_ticks = 0;
static uint16_t    rx_bit_accum   = 0;
static uint8_t     rx_bit_count   = 0;
static uint16_t    rx_dit_est     = 0;
static uint16_t    rx_threshold   = 0;
static uint8_t     rx_last_amp    = 0;

#define CW_RX_TIMEOUT_MULT  10u

/* REG_6F<6:0>: AF TX/RX input amplitude in dB — works for both OOK (beat note)
   and AF CW (keyed tone). No AGC guard needed; DSP updates it continuously. */
static uint8_t cw_get_af_amp(void)
{
    return BK4819_GetAfTxRx();
}

static void cw_rx_calibrate_threshold(void)
{
    uint32_t sum = 0;
    for (uint8_t i = 0; i < 10; i++) {
        sum += cw_get_af_amp();
        SYSTICK_DelayUs(500);
    }
    rx_threshold = (uint16_t)(sum / 10) + 10;   /* +10 dB headroom above noise floor */
}

static void cw_rx_update_dit_est(uint16_t new_dit)
{
    if (rx_dit_est == 0)
        rx_dit_est = new_dit;
    else
        rx_dit_est = (uint16_t)((rx_dit_est * 3u + new_dit) / 4u);
}

static uint16_t cw_dit_ref(void)
{
    if (rx_dit_est > 0) return rx_dit_est;
    uint16_t t = (uint16_t)(1200u / ((uint32_t)gEeprom.CW_WPM * 10u));
    return t > 0 ? t : 1;
}

static char cw_rx_decode(uint8_t len, uint16_t code)
{
    for (uint8_t i = 0; i < 26; i++) {
        if (morse_table[i].len == len && morse_table[i].code == code)
            return (char)('A' + i);
    }
    for (uint8_t i = 0; i < 10; i++) {
        if (morse_table[26 + i].len == len && morse_table[26 + i].code == code)
            return (char)('0' + i);
    }
    return '?';
}

static void cw_rx_decode_element(void)
{
    uint16_t dit    = cw_dit_ref();
    uint8_t  is_dah = (rx_mark_ticks >= dit * 2u) ? 1u : 0u;

    if (is_dah == 0)
        cw_rx_update_dit_est(rx_mark_ticks);

    if (rx_bit_count < 7) {
        rx_bit_accum = (uint16_t)((rx_bit_accum << 1) | is_dah);
        rx_bit_count++;
    }
}

static void cw_rx_commit_char(void)
{
    if (rx_bit_count == 0) return;

    char decoded  = cw_rx_decode(rx_bit_count, rx_bit_accum);
    cw_live_char  = decoded;
    rx_bit_accum  = 0;
    rx_bit_count  = 0;

    bool appended = false;
    if (cw_history_count > 0 &&
        cw_history[cw_history_count - 1].tag == CW_MSG_RX) {
        uint8_t idx = (uint8_t)(cw_history_count - 1u);
        uint8_t len = (uint8_t)strlen(cw_history[idx].text);
        if (len < CW_TEXT_COLS_FIRST) {
            cw_history[idx].text[len]     = decoded;
            cw_history[idx].text[len + 1] = '\0';
            appended = true;
        }
    }
    if (!appended) {
        char tmp[2] = { decoded, '\0' };
        cw_push_raw(tmp, CW_MSG_RX);
        if (cw_history_count > CW_VISIBLE_LINES)
            cw_scroll = (uint8_t)(cw_history_count - CW_VISIBLE_LINES);
    }
    gRequestDisplayScreen = DISPLAY_CW_CHAT;
}

static void cw_rx_commit_word_space(void)
{
    cw_rx_commit_char();
    if (cw_history_count > 0 &&
        cw_history[cw_history_count - 1].tag == CW_MSG_RX) {
        uint8_t idx = (uint8_t)(cw_history_count - 1u);
        uint8_t len = (uint8_t)strlen(cw_history[idx].text);
        if (len > 0 && cw_history[idx].text[len - 1] != ' ' &&
            len < CW_TEXT_COLS_FIRST) {
            cw_history[idx].text[len]     = ' ';
            cw_history[idx].text[len + 1] = '\0';
        }
    }
}

static void cw_rx_tick(void)
{
    if (gScreenToDisplay != DISPLAY_CW_CHAT) return;

    uint8_t raw_amp = cw_get_af_amp();
    rx_last_amp = (rx_last_amp == 0u) ? raw_amp
                : (uint8_t)((rx_last_amp * 3u + raw_amp) / 4u);

    /* Refresh bar at 20 Hz (every 5 ticks) — blitting costs ~7.5 ms at 1 MHz SPI.
       Set gUpdateDisplay directly; gRequestDisplayScreen can be swallowed if
       APP_Update() already cleared it earlier in the same loop iteration. */
    static uint8_t bar_refresh_ctr = 0;
    if (++bar_refresh_ctr >= 5u) {
        bar_refresh_ctr = 0;
        gUpdateDisplay = true;
    }

    if (tx_state != CW_TX_IDLE) return;

    uint16_t amp   = rx_last_amp;
    uint16_t dit   = cw_dit_ref();
    bool     above = (amp > rx_threshold);

    switch (rx_state) {

    case CW_RX_IDLE:
        if (above) {
            rx_mark_ticks  = 1;
            rx_space_ticks = 0;
            rx_state       = CW_RX_MARK;
        }
        break;

    case CW_RX_MARK:
        if (above) {
            rx_mark_ticks++;
        } else {
            rx_space_ticks = 1;
            rx_state       = CW_RX_SPACE;
        }
        break;

    case CW_RX_SPACE:
        if (above) {
            cw_rx_decode_element();
            if (rx_space_ticks >= dit * 8u)
                cw_rx_commit_word_space();
            else if (rx_space_ticks >= dit * 4u)
                cw_rx_commit_char();
            rx_mark_ticks  = 1;
            rx_space_ticks = 0;
            rx_state       = CW_RX_MARK;
        } else {
            rx_space_ticks++;
            if (rx_space_ticks >= dit * CW_RX_TIMEOUT_MULT) {
                cw_rx_decode_element();
                cw_rx_commit_word_space();
                rx_mark_ticks  = 0;
                rx_space_ticks = 0;
                rx_state       = CW_RX_IDLE;
                cw_live_char   = '\0';
            }
        }
        break;
    }
}

void CW_RX_SetThreshold(uint16_t rssi_threshold)
{
    rx_threshold = rssi_threshold;
    if (rx_threshold == 0)
        cw_rx_calibrate_threshold();
}

uint16_t CW_RX_GetThreshold(void)
{
    return rx_threshold;
}

uint8_t CW_RX_GetLastAmp(void)
{
    return rx_last_amp;
}

/* ------------------------------------------------------------------ */
/* State                                                               */
/* ------------------------------------------------------------------ */

CwHistoryEntry_t cw_history[CW_HISTORY_LINES];
uint8_t          cw_history_count = 0;
uint8_t          cw_scroll        = 0;
int8_t           cw_tx_recall     = -1;  /* index of selected TX entry, -1 = none */
char             cw_compose[CW_COMPOSE_MAX];
T9State_t        cw_t9;
char             cw_live_char     = '\0';
bool             cw_cursor_visible = true;

#define CW_T9_TIMEOUT_TICKS 100   /* 100 × 10ms = 1 second */
static uint8_t cw_t9_timeout = 0;

static const char * const cw_char_map[10] = {
    " 0",
    "!?/1",
    "ABC2", "DEF3", "GHI4", "JKL5",
    "MNO6", "PQRS7", "TUV8", "WXYZ9"
};

/* ------------------------------------------------------------------ */
/* History                                                             */
/* ------------------------------------------------------------------ */

static void cw_push_raw(const char *text, CwMsgTag_t tag)
{
    uint8_t idx;
    if (cw_history_count < CW_HISTORY_LINES) {
        idx = cw_history_count++;
    } else {
        memmove(&cw_history[0], &cw_history[1],
                (CW_HISTORY_LINES - 1) * sizeof(CwHistoryEntry_t));
        idx = CW_HISTORY_LINES - 1;
    }
    strncpy(cw_history[idx].text, text, CW_HISTORY_WIDTH - 1);
    cw_history[idx].text[CW_HISTORY_WIDTH - 1] = '\0';
    cw_history[idx].tag = tag;
}

void cw_history_push(const char *text, CwMsgTag_t tag)
{
    char chunk[CW_HISTORY_WIDTH];
    uint8_t len = (uint8_t)strlen(text);

    if (len <= CW_TEXT_COLS_FIRST) {
        cw_push_raw(text, tag);
    } else {
        strncpy(chunk, text, CW_TEXT_COLS_FIRST);
        chunk[CW_TEXT_COLS_FIRST] = '\0';
        cw_push_raw(chunk, tag);

        const char *p = text + CW_TEXT_COLS_FIRST;
        while (*p) {
            chunk[0] = ' ';
            strncpy(chunk + 1, p, CW_TEXT_COLS_CONT);
            chunk[1 + CW_TEXT_COLS_CONT] = '\0';
            uint8_t written = (uint8_t)strlen(chunk + 1);
            cw_push_raw(chunk, CW_MSG_CONT);
            p += written;
        }
    }

    if (cw_history_count > CW_VISIBLE_LINES)
        cw_scroll = cw_history_count - CW_VISIBLE_LINES;
    else
        cw_scroll = 0;
}

/* ------------------------------------------------------------------ */
/* TX recall helpers                                                   */
/* ------------------------------------------------------------------ */

static void cw_recall_build(char *out, uint8_t max, uint8_t idx)
{
    uint8_t pos = 0;
    /* First line — text stored verbatim */
    const char *p = cw_history[idx].text;
    while (*p && pos < max - 1) out[pos++] = *p++;
    /* Continuation lines — skip leading indent space */
    for (uint8_t j = idx + 1; j < cw_history_count && cw_history[j].tag == CW_MSG_CONT; j++) {
        const char *q = cw_history[j].text + 1;
        while (*q && pos < max - 1) out[pos++] = *q++;
    }
    out[pos] = '\0';
}

void CW_RecallText(char *out, uint8_t max)
{
    if (cw_tx_recall < 0 || (uint8_t)cw_tx_recall >= cw_history_count) {
        out[0] = '\0';
        return;
    }
    cw_recall_build(out, max, (uint8_t)cw_tx_recall);
}

/* ------------------------------------------------------------------ */
/* Init / timeslices                                                   */
/* ------------------------------------------------------------------ */

void CW_Init(void)
{
    static bool cw_ready = false;
    if (!cw_ready) {
        /* First entry only — clear history, compose, and load prediction counts */
        memset(cw_history, 0, sizeof(cw_history));
        cw_history_count = 0;
        cw_scroll        = 0;
        cw_pred_load();
        cw_ready         = true;
    }
    cw_popup_active   = false;
    cw_tx_recall      = -1;
    cw_live_char      = '\0';
    cw_cursor_visible = true;
    rx_state          = CW_RX_IDLE;
    rx_dit_est        = 0;
    cw_rx_calibrate_threshold();   /* sample noise floor each time screen is entered */
    T9_Init(&cw_t9, cw_compose, CW_COMPOSE_MAX, cw_char_map);
}

void CW_TX_Start(const char *text)
{
    if (tx_state != CW_TX_IDLE) return;

    uint16_t wpm = gEeprom.CW_WPM;
    if (wpm < 5)  wpm = 5;
    if (wpm > 40) wpm = 40;

    dit_ticks  = (uint16_t)(1200u / ((uint32_t)wpm * 10u));
    if (dit_ticks < 1) dit_ticks = 1;
    dah_ticks  = dit_ticks * 3;
    cgap_ticks = dit_ticks * 3;
    wgap_ticks = dit_ticks * 7;

    strncpy(tx_buf, text, CW_COMPOSE_MAX - 1);
    tx_buf[CW_COMPOSE_MAX - 1] = '\0';
    tx_ptr  = tx_buf;
    tx_tick = 0;

    tx_state = CW_TX_ARMING;
    /* TX hardware is armed by GENERIC_Key_PTT(true) called from CW_ProcessKeys */
}

bool CW_TX_Active(void)
{
    return tx_state != CW_TX_IDLE;
}

void CW_TimeSlice10ms(void)
{
    if (cw_t9.last_key != 255 && cw_t9_timeout > 0) {
        if (--cw_t9_timeout == 0) {
            T9_Commit(&cw_t9);
            gRequestDisplayScreen = DISPLAY_CW_CHAT;
        }
    }
    cw_tx_tick();
    cw_rx_tick();
}

void CW_TimeSlice500ms(void)
{
    if (gScreenToDisplay != DISPLAY_CW_CHAT)
        return;
    cw_cursor_visible     = !cw_cursor_visible;
    gRequestDisplayScreen = DISPLAY_CW_CHAT;
}

/* ------------------------------------------------------------------ */
/* Key handler                                                         */
/* ------------------------------------------------------------------ */

void CW_ProcessKeys(KEY_Code_t Key, bool bKeyPressed, bool bKeyHeld)
{
    if (!bKeyPressed && !bKeyHeld)
        return;

    switch (Key) {
    case KEY_1:
        if (!bKeyHeld) {
            if (!cw_popup_active) {
                cw_popup_active = true;
                cw_popup_sel    = 0;
                /* clamp in case effective count shrank since last open */
                if (cw_popup_sel >= cw_pred_effective()) cw_popup_sel = 0;
            } else {
                cw_pred_insert(cw_pred_order[cw_popup_sel]);
            }
        }
        gRequestDisplayScreen = DISPLAY_CW_CHAT;
        return;

    case KEY_0:
    case KEY_2 ... KEY_9:
        cw_popup_active = false;
        cw_tx_recall = -1;
        T9_Key(&cw_t9, Key, bKeyHeld);
        cw_t9_timeout = CW_T9_TIMEOUT_TICKS;
        break;

    case KEY_STAR:
        cw_tx_recall = -1;
        T9_Backspace(&cw_t9);
        cw_t9_timeout = 0;
        break;

    case KEY_PTT:
        if (!bKeyPressed) {
            GENERIC_Key_PTT(false);
        } else if (!bKeyHeld && !CW_TX_Active()) {
            /* Only arm TX if there is actually something to send */
            bool started = false;
            if (cw_tx_recall >= 0) {
                char recall_buf[CW_COMPOSE_MAX];
                cw_recall_build(recall_buf, sizeof(recall_buf), (uint8_t)cw_tx_recall);
                if (recall_buf[0] != '\0') {
                    if (gEeprom.CW_FLAGS & CW_FLAG_RECALL_HISTORY)
                        cw_history_push(recall_buf, CW_MSG_TX);
                    CW_TX_Start(recall_buf);
                    cw_tx_recall = -1;
                    started = true;
                }
            } else {
                T9_Commit(&cw_t9);
                if (strlen(cw_compose) > 0) {
                    cw_history_push(cw_compose, CW_MSG_TX);
                    CW_TX_Start(cw_compose);
                    T9_Reset(&cw_t9);
                    started = true;
                }
            }
            if (started)
                GENERIC_Key_PTT(true);
        }
        gRequestDisplayScreen = DISPLAY_CW_CHAT;
        return;

    case KEY_MENU:
        if (!bKeyHeld) {
            MENU_SetReturnDisplay(DISPLAY_CW_CHAT);
            gMenuCursor = UI_MENU_GetMenuIdx(MENU_CW_SPEED);
            MENU_ShowCurrentSetting();
            gRequestDisplayScreen = DISPLAY_MENU;
            return;
        }
        break;

    case KEY_UP: {
        if (!bKeyHeld && cw_popup_active) {
            uint8_t n = cw_pred_effective();
            cw_popup_sel = (cw_popup_sel == 0) ? (uint8_t)(n - 1u) : cw_popup_sel - 1u;
            break;
        }
        if (bKeyHeld) {
            cw_scroll    = 0;
            cw_tx_recall = -1;
            break;
        }
        /* Navigate to the next older TX entry; fallback to plain scroll */
        int8_t start = (cw_tx_recall > 0) ? (int8_t)(cw_tx_recall - 1)
                                           : (int8_t)(cw_history_count - 1);
        bool found = false;
        for (int8_t j = start; j >= 0; j--) {
            if (cw_history[(uint8_t)j].tag == CW_MSG_TX) {
                cw_tx_recall = j;
                if ((uint8_t)j < cw_scroll) cw_scroll = (uint8_t)j;
                else if ((uint8_t)j >= cw_scroll + CW_VISIBLE_LINES)
                    cw_scroll = (uint8_t)j - CW_VISIBLE_LINES + 1;
                found = true;
                break;
            }
        }
        if (!found && cw_scroll > 0) cw_scroll--;
        break;
    }

    case KEY_DOWN: {
        if (!bKeyHeld && cw_popup_active) {
            uint8_t n = cw_pred_effective();
            cw_popup_sel = (cw_popup_sel >= (uint8_t)(n - 1u)) ? 0u : cw_popup_sel + 1u;
            break;
        }
        if (bKeyHeld) {
            cw_scroll    = (cw_history_count > CW_VISIBLE_LINES)
                           ? cw_history_count - CW_VISIBLE_LINES : 0;
            cw_tx_recall = -1;
            break;
        }
        /* Navigate to the next newer TX entry; fallback to plain scroll */
        uint8_t start = (cw_tx_recall >= 0) ? (uint8_t)(cw_tx_recall + 1) : 0;
        bool found = false;
        for (uint8_t j = start; j < cw_history_count; j++) {
            if (cw_history[j].tag == CW_MSG_TX) {
                cw_tx_recall = (int8_t)j;
                if (j < cw_scroll) cw_scroll = j;
                else if (j >= cw_scroll + CW_VISIBLE_LINES)
                    cw_scroll = j - CW_VISIBLE_LINES + 1;
                found = true;
                break;
            }
        }
        if (!found && cw_scroll + CW_VISIBLE_LINES < cw_history_count) cw_scroll++;
        break;
    }

    case KEY_EXIT:
        if (cw_popup_active) {
            cw_popup_active = false;
            gRequestDisplayScreen = DISPLAY_CW_CHAT;
            return;
        }
        if (!bKeyHeld && cw_tx_recall >= 0) {
            cw_tx_recall = -1;  /* cancel recall first */
        } else if (!bKeyHeld && cw_t9.last_key != 255) {
            T9_Commit(&cw_t9);
            cw_t9_timeout = 0;
        } else if (!bKeyHeld && strlen(cw_compose) > 0) {
            T9_Reset(&cw_t9);
            cw_t9_timeout = 0;
        } else {
            gRequestDisplayScreen = DISPLAY_MAIN;
        }
        return;

    case KEY_F:
        /* Manual RX threshold adjustment: short press = +2 dB, held = -2 dB */
        if (!bKeyHeld) {
            if (rx_threshold < 61u) rx_threshold += 2u;
        } else {
            if (rx_threshold > 2u)  rx_threshold -= 2u;
        }
        break;

    default:
        break;
    }

    gRequestDisplayScreen = DISPLAY_CW_CHAT;
}

#endif /* ENABLE_FEAT_ELW_CW */
