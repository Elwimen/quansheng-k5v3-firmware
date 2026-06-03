#ifdef ENABLE_FEAT_ELW_CW

#include <string.h>
#include "app/cw.h"
#include "app/t9.h"
#include "settings.h"
#include "driver/bk4819.h"
#include "driver/bk4819-regs.h"
#include "radio.h"
#include "app/generic.h"
#include "functions.h"
#include "misc.h"

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
        BK4819_ExitDTMF_TX(false);
        tx_state = CW_TX_IDLE;
        gRequestDisplayScreen = DISPLAY_CW_CHAT;
        return;
    }

    if (tx_tick > 0) { tx_tick--; return; }

    switch (tx_state) {

    case CW_TX_ARMING:
        /* Wait here until FUNCTION_TRANSMIT becomes active */
        if (gCurrentFunction != FUNCTION_TRANSMIT) break;
        /* TX just became active — init tone generator */
        BK4819_EnterTxMute();
        BK4819_WriteRegister(BK4819_REG_70,
            BK4819_REG_70_MASK_ENABLE_TONE1 | (66u << BK4819_REG_70_SHIFT_TONE1_TUNING_GAIN));
        BK4819_WriteRegister(BK4819_REG_71,
            (uint16_t)(((uint32_t)gEeprom.CW_TONE_HZ * 1353245u + (1u << 16)) >> 17));
        BK4819_SetAF(BK4819_AF_MUTE);
        BK4819_EnableTXLink();
        tx_tick = 5;    /* 50ms settle before first element */
        tx_state = CW_TX_ELEMENT_ON;
        cw_tx_load_next_char();
        break;

    case CW_TX_ELEMENT_ON: {
        bool is_dah = (tx_elem_code >> (tx_elem_len - 1 - tx_elem_idx)) & 1;
        BK4819_ExitTxMute();
        tx_tick  = is_dah ? dah_ticks : dit_ticks;
        tx_state = CW_TX_ELEMENT_OFF;
        break;
    }

    case CW_TX_ELEMENT_OFF:
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
        /* Message finished — stop tone, keep TX up until user releases PTT */
        BK4819_ExitDTMF_TX(false);
        tx_state = CW_TX_IDLE;
        gRequestDisplayScreen = DISPLAY_CW_CHAT;
        break;

    default:
        break;
    }
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
        /* First entry only — clear history and compose buffer */
        memset(cw_history, 0, sizeof(cw_history));
        cw_history_count = 0;
        cw_scroll        = 0;
        cw_ready         = true;
    }
    cw_tx_recall      = -1;
    cw_live_char      = '\0';
    cw_cursor_visible = true;
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
    /* Phase 4: cw_rx_tick(); */
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
    case KEY_0 ... KEY_9:
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
        if (bKeyPressed && !bKeyHeld && !CW_TX_Active()) {
            if (cw_tx_recall >= 0) {
                /* Re-transmit recalled history entry */
                char recall_buf[CW_COMPOSE_MAX];
                cw_recall_build(recall_buf, sizeof(recall_buf), (uint8_t)cw_tx_recall);
                if (recall_buf[0] != '\0') {
                    if (gEeprom.CW_FLAGS & CW_FLAG_RECALL_HISTORY)
                        cw_history_push(recall_buf, CW_MSG_TX);
                    CW_TX_Start(recall_buf);
                    cw_tx_recall = -1;
                }
            } else {
                T9_Commit(&cw_t9);
                if (strlen(cw_compose) > 0) {
                    cw_history_push(cw_compose, CW_MSG_TX);
                    CW_TX_Start(cw_compose);
                    T9_Reset(&cw_t9);
                }
            }
        }
        GENERIC_Key_PTT(bKeyPressed);
        gRequestDisplayScreen = DISPLAY_CW_CHAT;
        return;

    case KEY_MENU:
        if (!bKeyHeld) {
            cw_tx_recall = -1;
            T9_Reset(&cw_t9);
        }
        break;

    case KEY_UP: {
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

    default:
        break;
    }

    gRequestDisplayScreen = DISPLAY_CW_CHAT;
}

#endif /* ENABLE_FEAT_ELW_CW */
