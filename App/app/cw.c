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
static bool    cw_pred_dirty  = false;   /* pending save — flushed at TX end */

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
    cw_pred_dirty = true;   /* flush to EEPROM at TX end, away from SPI/keyboard conflicts */

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

    /* A loaded tx_tick of N must last N ticks. The tick that ends the wait also runs the
       next state, so decrement first and fall through on the tick that reaches zero --
       returning here as well would spend one extra tick, making every element and gap
       10ms too long (a 15 WPM dot came out 90ms instead of 80, and a dash 250ms instead
       of 240, so dash/dot was 2.78 rather than 3). */
    if (tx_tick > 0) {
        if (--tx_tick > 0)
            return;
    }

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
        if (cw_pred_dirty) {
            cw_pred_save();
            cw_pred_dirty = false;
        }
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
static bool        rx_wrap_pending = false;  /* line filled at a word boundary: next word -> new line */

/* "Is this really Morse?" confidence gate. Because the decoder now runs in the background on
   any open squelch (incl. FM voice), characters are held pending until a run of clean ones
   confirms real Morse; only then are they flushed to the history and shown. A transmission
   that never reaches confidence (voice/noise) is discarded -- it never pollutes the log or
   the main-screen line. */
#define CW_RX_DETECT_CHARS  3u        /* valid (non-'?') chars needed to accept a transmission */
#define CW_RX_SHOW_HOLD_MS  4000u     /* keep the decode on the main-screen line this long */
static char        rx_pending[12];
static uint8_t     rx_pending_len = 0;
static uint8_t     rx_valid_count = 0;
static bool        rx_detected    = false;   /* this transmission confirmed as Morse */
static uint16_t    rx_show_ms     = 0;        /* main-screen line hold, ms (decremented @10ms) */

/* Rhythm scope: a ring of the debounced on/off signal, one sample every
   CW_SCOPE_DECIM ms, so 128 columns cover ~1s -- enough to see the Morse rhythm. */
static uint8_t rx_scope[CW_SCOPE_LEN];   /* 0/1 per column, oldest..newest via head */
static uint8_t rx_scope_head = 0;        /* index of the next slot to write */
static uint8_t rx_scope_div  = 0;

#define CW_RX_TIMEOUT_MULT  10u

/* An amplitude edge must persist this many 1ms samples before it counts, so a noise blip
   shorter than any real element is rejected. Must stay below the briefest real element --
   a 40 WPM dit is 30ms -- with margin. The debounce is symmetric, so element durations are
   preserved. */
#define CW_RX_DEBOUNCE_MS   6u

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
    /* Milliseconds: the receiver is sampled every 1ms now, so a dit is 1200/WPM ms. */
    uint16_t t = (uint16_t)(1200u / (uint32_t)gEeprom.CW_WPM);
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

/* ---- speed acquisition -----------------------------------------------------------
   The dit length is the clock of the whole message, and until it is known nothing can be
   classified: a dah measured against a stale dit reads as a dit, and one wrong element
   poisons the running average that produced it. So do not guess from the first element --
   buffer the first few characters, estimate the dit from them, then decode the buffer.

   The estimate uses marks *and* gaps: a dit-mark and an element gap are both exactly one
   dit, so the shortest of either is the clock, and averaging everything close to it beats
   trusting a single shortest sample. Each transmission re-acquires, so the decoder follows
   a station that changes speed instead of dragging its old estimate along. */
#define CW_RX_ACQ_MARKS  6u    /* about two or three characters */
#define CW_RX_ACQ_MAX   24u

static uint16_t rx_acq_dur[CW_RX_ACQ_MAX];
static uint8_t  rx_acq_is_mark[CW_RX_ACQ_MAX];
static uint8_t  rx_acq_n     = 0;
static uint8_t  rx_acq_marks = 0;
static bool     rx_locked    = false;

static void cw_rx_acq_reset(void)
{
    rx_acq_n     = 0;
    rx_acq_marks = 0;
    rx_locked    = false;
    /* Each transmission must re-prove itself as Morse; discard any unconfirmed pending
       chars (they were voice/noise). rx_show_ms is left to decay so a confirmed message
       lingers on the main-screen line after the sender stops. */
    rx_pending_len = 0;
    rx_valid_count = 0;
    rx_detected    = false;
}

static void cw_rx_acq_push(uint16_t duration, bool is_mark)
{
    if (rx_acq_n >= CW_RX_ACQ_MAX) return;
    rx_acq_dur[rx_acq_n]     = duration;
    rx_acq_is_mark[rx_acq_n] = is_mark ? 1u : 0u;
    rx_acq_n++;
    if (is_mark) rx_acq_marks++;
}

static uint16_t cw_rx_acq_estimate(void)
{
    /* The clock is the shortest thing in the message -- but not blindly: one clipped edge
       or noise blip is shorter than any real element, and taking it as the dit halves the
       timebase, at which point every dit measures as a dah and the message reads as a row
       of T's. So require the candidate to be corroborated: a dit is not a dit unless at
       least two elements agree with it. In 2-3 characters there are always several. */
    uint16_t best = 0;
    for (uint8_t i = 0; i < rx_acq_n; i++) {
        uint16_t cand = rx_acq_dur[i];
        if (cand == 0 || (best != 0 && cand >= best)) continue;

        uint8_t support = 0;
        for (uint8_t j = 0; j < rx_acq_n; j++) {
            if (rx_acq_dur[j] * 2u <= cand * 3u)   /* within 1.5x of the candidate */
                support++;
        }
        if (support >= 2u) best = cand;
    }
    if (best == 0) return cw_dit_ref();

    /* Average the whole short class: a dit-mark and an element gap are both one dit, so
       everything under twice the shortest is a sample of the same quantity. */
    uint32_t sum = 0;
    uint8_t  n   = 0;
    for (uint8_t i = 0; i < rx_acq_n; i++) {
        if (rx_acq_dur[i] < best * 2u) { sum += rx_acq_dur[i]; n++; }
    }
    return n ? (uint16_t)(sum / n) : best;
}

static void cw_rx_decode_element(void);
static void cw_rx_commit_char(void);
static void cw_rx_commit_word_space(void);

/* Estimate the dit from what we buffered, then decode the buffer with it. */
static void cw_rx_acq_lock(void)
{
    uint16_t dit = cw_rx_acq_estimate();
    rx_dit_est = dit;

    for (uint8_t i = 0; i < rx_acq_n; i++) {
        if (rx_acq_is_mark[i]) {
            rx_mark_ticks = rx_acq_dur[i];
            cw_rx_decode_element();
        } else {
            uint16_t gap = rx_acq_dur[i];
            if (gap * 2u >= dit * 9u)         /* 4.5 dits: a word */
                cw_rx_commit_word_space();
            else if (gap * 4u >= dit * 7u)    /* 1.75 dits: a character */
                cw_rx_commit_char();
        }
    }
    rx_acq_n     = 0;
    rx_acq_marks = 0;
    rx_locked    = true;
}

static void cw_rx_decode_element(void)
{
    uint16_t dit    = cw_dit_ref();
    /* A decision boundary belongs at the geometric mean of the two classes it separates,
       so a dit (1) and a dah (3) are split at sqrt(3) = 1.73 dits, not at 2. */
    uint8_t  is_dah = (rx_mark_ticks * 4u >= dit * 7u) ? 1u : 0u;   /* 1.75 dits */

    if (is_dah == 0)
        cw_rx_update_dit_est(rx_mark_ticks);

    if (rx_bit_count < 7) {
        rx_bit_accum = (uint16_t)((rx_bit_accum << 1) | is_dah);
        rx_bit_count++;
    }
}

/* Append one already-confirmed character to the RX history, word-wrapping on spaces. */
static void cw_hist_append(char decoded)
{
    bool have_rx = (cw_history_count > 0 &&
                    cw_history[cw_history_count - 1].tag == CW_MSG_RX);

    if (rx_wrap_pending) {
        rx_wrap_pending = false;
        char tmp[2] = { decoded, '\0' };
        cw_push_raw(tmp, CW_MSG_RX);
    } else if (have_rx) {
        uint8_t idx  = (uint8_t)(cw_history_count - 1u);
        char   *line = cw_history[idx].text;
        uint8_t len  = (uint8_t)strlen(line);
        if (len < CW_TEXT_COLS_FIRST) {
            line[len]     = decoded;
            line[len + 1] = '\0';
            return;                                  /* fit on the current line, no scroll */
        }
        /* Line full mid-word -- wrap on the word boundary (move the trailing run down). */
        int8_t sp = -1;
        for (int8_t i = (int8_t)len - 1; i >= 0; i--)
            if (line[i] == ' ') { sp = i; break; }
        if (sp >= 0) {
            char    word[CW_TEXT_COLS_FIRST + 2];
            uint8_t wl = 0;
            for (uint8_t i = (uint8_t)(sp + 1); i < len; i++)
                word[wl++] = line[i];
            word[wl++] = decoded;
            word[wl]   = '\0';
            line[sp]   = '\0';
            cw_push_raw(word, CW_MSG_RX);
        } else {
            char tmp[2] = { decoded, '\0' };
            cw_push_raw(tmp, CW_MSG_RX);
        }
    } else {
        char tmp[2] = { decoded, '\0' };
        cw_push_raw(tmp, CW_MSG_RX);
    }
    if (cw_history_count > CW_VISIBLE_LINES)
        cw_scroll = (uint8_t)(cw_history_count - CW_VISIBLE_LINES);
}

/* Refresh whichever screen shows the decode -- but never yank the user to the CW chat
   screen from the background/main. The main-screen center line refreshes via rx_show_ms. */
static void cw_rx_note_display(void)
{
    rx_show_ms = (uint16_t)(CW_HoldSeconds() * 1000u);
    if (gScreenToDisplay == DISPLAY_CW_CHAT)
        gRequestDisplayScreen = DISPLAY_CW_CHAT;
}

static void cw_rx_commit_char(void)
{
    if (rx_bit_count == 0) return;

    char decoded  = cw_rx_decode(rx_bit_count, rx_bit_accum);
    cw_live_char  = decoded;
    rx_bit_accum  = 0;
    rx_bit_count  = 0;

    if (!rx_detected) {
        /* Hold the character until the transmission proves itself Morse. */
        if (rx_pending_len < sizeof(rx_pending) - 1u)
            rx_pending[rx_pending_len++] = decoded;
        if (decoded != '?')
            rx_valid_count++;
        if (rx_valid_count >= CW_RX_DETECT_CHARS) {   /* confirmed -> flush the held chars */
            rx_detected = true;
            for (uint8_t i = 0; i < rx_pending_len; i++)
                cw_hist_append(rx_pending[i]);
            rx_pending_len = 0;
            cw_rx_note_display();
        }
        return;
    }
    cw_hist_append(decoded);
    cw_rx_note_display();
}

static void cw_rx_commit_word_space(void)
{
    cw_rx_commit_char();
    if (!rx_detected) {
        /* Word boundary while still unconfirmed -- keep it in the pending buffer. */
        if (rx_pending_len > 0 && rx_pending_len < sizeof(rx_pending) - 1u &&
            rx_pending[rx_pending_len - 1] != ' ')
            rx_pending[rx_pending_len++] = ' ';
        return;
    }
    if (cw_history_count > 0 &&
        cw_history[cw_history_count - 1].tag == CW_MSG_RX) {
        uint8_t idx = (uint8_t)(cw_history_count - 1u);
        uint8_t len = (uint8_t)strlen(cw_history[idx].text);
        if (len > 0 && cw_history[idx].text[len - 1] != ' ' &&
            len < CW_TEXT_COLS_FIRST) {
            cw_history[idx].text[len]     = ' ';
            cw_history[idx].text[len + 1] = '\0';
        } else if (len >= CW_TEXT_COLS_FIRST) {
            rx_wrap_pending = true;
        }
    }
}

/* CW_FLAGS bits 1-2: 0 = decode only on the CW chat screen, 1 = also on the main screen,
   2 = everywhere (full background). */
uint8_t CW_MonScope(void)
{
    return (uint8_t)((gEeprom.CW_FLAGS & CW_FLAG_MON_MASK) >> CW_FLAG_MON_SHIFT);
}

/* How long (seconds) a decoded message stays on the main-screen line after the sender stops.
   Stored in CW_FLAGS bits 3-7; 0 (un-set) -> 4s default. */
uint8_t CW_HoldSeconds(void)
{
    uint8_t s = (uint8_t)((gEeprom.CW_FLAGS & CW_FLAG_HOLD_MASK) >> CW_FLAG_HOLD_SHIFT);
    return s ? s : 4u;
}

/* CW speed presets -- named wpm+tone bundles the CWSpd menu offers alongside custom speeds.
   Defined once here (was duplicated between app/menu.c and ui/menu.c). */
typedef struct { uint8_t wpm; uint16_t tone; const char *name; } CwPreset_t;
static const CwPreset_t cw_presets[] = {
    {10, 600, "SLOW"},
    {15, 700, "STD"},
    {20, 700, "QSO"},
    {25, 800, "FAST"},
    {35, 900, "CONTEST"},
};

uint8_t CW_PresetCount(void) { return (uint8_t)(sizeof(cw_presets) / sizeof(cw_presets[0])); }
uint8_t CW_PresetWpm(uint8_t i)  { return (i < CW_PresetCount()) ? cw_presets[i].wpm : 15u; }
const char *CW_PresetName(uint8_t i) { return (i < CW_PresetCount()) ? cw_presets[i].name : ""; }

void CW_ApplyPreset(uint8_t i)
{
    if (i < CW_PresetCount()) {
        gEeprom.CW_WPM     = cw_presets[i].wpm;
        gEeprom.CW_TONE_HZ = cw_presets[i].tone;
    }
}

/* Index of the preset whose wpm+tone match the current settings, or -1 (custom). */
int8_t CW_PresetMatch(void)
{
    for (uint8_t i = 0; i < CW_PresetCount(); i++)
        if (cw_presets[i].wpm == gEeprom.CW_WPM && cw_presets[i].tone == gEeprom.CW_TONE_HZ)
            return (int8_t)i;
    return -1;
}

void CW_RX_Sample(void)
{
    if (tx_state != CW_TX_IDLE) return;   /* never poke the bus while we are keying */

    /* Run per the CWMon scope: chat screen always; main screen if scope>=1; anywhere if
       scope==2. The squelch-arming + confidence gates below keep it from decoding noise or
       voice into anything visible, so running broadly is cheap and safe. */
    const uint8_t scope = CW_MonScope();
    if (gScreenToDisplay != DISPLAY_CW_CHAT &&
        !(scope >= 2u) &&
        !(scope == 1u && gScreenToDisplay == DISPLAY_MAIN))
        return;

    /* The state machine counts milliseconds, so it has to run on every tick even when the
       amplitude cannot be sampled: the main loop bit-bangs the same chip, and an interrupt
       landing mid-frame would corrupt both transactions. Skipping the whole tick instead
       would drop time, not just a sample -- and at 40 WPM a dot is only 30 ticks, so a
       handful of skips is enough to shrink an element into the next class. Hold the last
       reading and keep counting. */
    static uint8_t last_amp = 0;
    uint8_t raw_amp;
    if (gBK4819_BusBusy) {
        raw_amp = last_amp;
    } else {
        raw_amp = cw_get_af_amp();
        last_amp = raw_amp;
    }

    /* Smoothed only for the on-screen bar. Detection uses the raw amplitude: this filter
       settles in ~3.5 samples = 35ms, which is comparable to a whole dot at 15 WPM, and it
       decays slower than it rises -- so it detected the start of a mark late and its end
       later still, stretching every mark and shrinking every gap. That is what turned dots
       into dashes and ran characters together. */
    rx_last_amp = (rx_last_amp == 0u) ? raw_amp
                : (uint8_t)((rx_last_amp * 3u + raw_amp) / 4u);


    uint16_t dit   = cw_dit_ref();

    /* Schmitt trigger on the raw amplitude: it takes a clearly stronger signal to call the
       start of a mark than it does to keep one going. A single threshold on a noisy
       envelope chatters around the crossing and smears the edge whose timing is the whole
       measurement. The margin is a quarter of the threshold above the noise floor. */
    uint16_t hyst      = (rx_threshold > 4u) ? (rx_threshold / 4u) : 1u;
    bool     raw_above = (rx_state == CW_RX_MARK)
                       ? (raw_amp + hyst > rx_threshold)   /* stay in the mark */
                       : (raw_amp > rx_threshold + hyst);  /* start a new one */

    /* Min-element noise gate: debounce the edge. REG_6F has no tone selectivity, so band
       noise and QRN poke above the threshold for a millisecond or two and the timer reads
       the blip as a one-dit element -- the stream of E's seen on the air. Require an edge to
       persist CW_RX_DEBOUNCE_MS before it counts; shorter blips are dropped. Because both
       edges are delayed equally, the measured mark/gap durations are unchanged. */
    static bool    db_above = false;
    static uint8_t db_count = 0;
    if (raw_above != db_above) {
        if (++db_count >= CW_RX_DEBOUNCE_MS) { db_above = raw_above; db_count = 0; }
    } else {
        db_count = 0;
    }
    bool above = db_above;

    /* Feed the rhythm scope at a decimated rate (see rx_scope). */
    if (++rx_scope_div >= CW_SCOPE_DECIM) {
        rx_scope_div = 0;
        rx_scope[rx_scope_head] = above ? 1u : 0u;
        rx_scope_head = (uint8_t)((rx_scope_head + 1u) % CW_SCOPE_LEN);
    }

    switch (rx_state) {

    case CW_RX_IDLE:
        /* Squelch-arming gate: only *start* a reception when the receiver's own squelch says
           a real carrier is present. The squelch (RSSI + noise + glitch thresholds) rejects
           band noise far better than the raw broadband amplitude, so this is what stops the
           decoder free-running on an empty channel. It gates the start only: an OOK carrier
           is keyed, so the squelch closes in every inter-element gap -- once we are receiving,
           amplitude drives the timing and the gaps are measured normally. The end-of-
           transmission timeout returns here and re-arms for the next station. */
        if (above && g_SquelchLost) {
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
            if (!rx_locked) {
                /* Still learning the speed: keep the element and the gap that followed it,
                   and decode nothing yet. */
                cw_rx_acq_push(rx_mark_ticks, true);
                cw_rx_acq_push(rx_space_ticks, false);
                if (rx_acq_marks >= CW_RX_ACQ_MARKS || rx_acq_n + 2u > CW_RX_ACQ_MAX)
                    cw_rx_acq_lock();
            } else {
            cw_rx_decode_element();
            /* Morse spaces elements by 1 dit, characters by 3 and words by 7. The old
               thresholds (4 and 8) sat *above* the classes they were meant to separate, so
               a correctly timed sender never cleared them and its characters ran together.
               Split each pair at its geometric mean instead: sqrt(3) = 1.73 between an
               element gap and a character gap, sqrt(21) = 4.58 between a character and a
               word. */
            if (rx_space_ticks * 2u >= dit * 9u)         /* 4.5 dits */
                cw_rx_commit_word_space();
            else if (rx_space_ticks * 4u >= dit * 7u)    /* 1.75 dits */
                cw_rx_commit_char();
            }
            rx_mark_ticks  = 1;
            rx_space_ticks = 0;
            rx_state       = CW_RX_MARK;
        } else {
            rx_space_ticks++;
            if (rx_space_ticks >= dit * CW_RX_TIMEOUT_MULT) {
                /* End of the transmission. If it was too short to have locked the speed --
                   a bare "K" is three elements -- estimate from what little there is and
                   decode it now rather than throwing it away. */
                if (!rx_locked) {
                    cw_rx_acq_push(rx_mark_ticks, true);
                    cw_rx_acq_lock();
                } else {
                    cw_rx_decode_element();
                }
                cw_rx_commit_word_space();
                rx_mark_ticks  = 0;
                rx_space_ticks = 0;
                rx_state       = CW_RX_IDLE;
                cw_live_char   = '\0';
                /* Re-acquire on the next transmission: the other station may be sending at
                   a different speed, and the old estimate is worse than no estimate. */
                cw_rx_acq_reset();
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

/* Detected sender speed in WPM (0 = not yet acquired). dit_ms = 1200/WPM. */
uint8_t CW_RX_GetWpm(void)
{
    return rx_dit_est ? (uint8_t)(1200u / rx_dit_est) : 0u;
}

/* S-meter with peak-hold. An OOK carrier is keyed, so raw RSSI strobes S0<->Sx with every
   dit; peak-hold jumps up instantly, holds ~0.8s, then decays -- a steady, readable meter.
   Sampled from CW_TimeSlice10ms (main loop, bus-safe via gBK4819_BusBusy). */
static uint8_t rx_slevel      = 0;   /* held peak, 0..9 */
static uint8_t rx_slevel_hold = 0;

static uint8_t cw_rx_slevel_now(void)
{
    int16_t dbm = BK4819_GetRSSI_dBm();
#ifdef ENABLE_FEAT_F4HWN
    if (gCurrentVfo != NULL)
        dbm += dBmCorrTable[gCurrentVfo->Band];
#endif
    if (dbm >= -93)  return 9u;
    if (dbm < -141)  return 0u;
    return (uint8_t)((dbm + 147) / 6);
}

void CW_RX_UpdateSMeter(void)
{
    uint8_t now = cw_rx_slevel_now();
    if (now >= rx_slevel) {
        rx_slevel      = now;
        rx_slevel_hold = 80u;                 /* hold the peak ~0.8s (80 x 10ms) */
    } else if (rx_slevel_hold > 0u) {
        rx_slevel_hold--;
    } else if (rx_slevel > now) {
        rx_slevel--;                          /* decay one S-unit per tick after the hold */
    }
}

uint8_t CW_RX_GetSLevel(void)
{
    return rx_slevel;
}

/* True while a confirmed Morse decode should be shown on the main-screen line. */
bool CW_RX_Detected(void)
{
    return rx_show_ms > 0u;
}

/* Tail of the most recent decoded RX message for the one-line main-screen display:
   copies the last n-1 chars of the newest RX history entry, NUL-terminated. */
void CW_RX_GetTail(char *buf, uint8_t n)
{
    buf[0] = '\0';
    for (int i = (int)cw_history_count - 1; i >= 0; i--) {
        if (cw_history[i].tag == CW_MSG_RX) {
            const char *t   = cw_history[i].text;
            uint8_t     len = (uint8_t)strlen(t);
            const char *tail = t + (len > (n - 1u) ? len - (n - 1u) : 0);
            strncpy(buf, tail, n - 1u);
            buf[n - 1u] = '\0';
            return;
        }
    }
}

/* 0 = idle (nothing heard), 1 = mark (key down now), 2 = space (in a gap). */
uint8_t CW_RX_GetState(void)
{
    return (uint8_t)rx_state;
}

/* The element being assembled right now, as dits/dahs. Writes at most n-1 chars
   ('.'=dit, '-'=dah, MSB first) and NUL-terminates. Empty when nothing is pending. */
void CW_RX_GetLivePattern(char *buf, uint8_t n)
{
    uint8_t count = rx_bit_count;
    if (count > n - 1u) count = (uint8_t)(n - 1u);
    for (uint8_t i = 0; i < count; i++)
        buf[i] = (rx_bit_accum & (1u << (rx_bit_count - 1u - i))) ? '-' : '.';
    buf[count] = '\0';
}

/* Rhythm scope: oldest..newest into buf (CW_SCOPE_LEN entries, 0/1). */
void CW_RX_GetScope(uint8_t *buf)
{
    for (uint8_t i = 0; i < CW_SCOPE_LEN; i++)
        buf[i] = rx_scope[(rx_scope_head + i) % CW_SCOPE_LEN];
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
    if (gEeprom.CW_RX_THRESHOLD > 0u) {
        rx_threshold = gEeprom.CW_RX_THRESHOLD;
    } else {
        cw_rx_calibrate_threshold();
        gEeprom.CW_RX_THRESHOLD = rx_threshold;
        SETTINGS_SaveCwThreshold();
    }
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

    /* The background/main-screen decoder needs an RX threshold even if the CW chat screen
       (which calls CW_Init) was never opened. Prefer the persisted value; otherwise calibrate
       once, but only while the channel is quiet so we measure the true noise floor. This runs
       in the main loop, so the blocking calibrate is safe here (never in the 1ms ISR). */
    if (rx_threshold == 0u && CW_MonScope() >= 1u) {
        if (gEeprom.CW_RX_THRESHOLD > 0u) {
            rx_threshold = gEeprom.CW_RX_THRESHOLD;
        } else if (!g_SquelchLost) {
            cw_rx_calibrate_threshold();
            gEeprom.CW_RX_THRESHOLD = rx_threshold;
            SETTINGS_SaveCwThreshold();
        }
    }

    /* Main-screen CW line: hold a confirmed decode for CW_RX_SHOW_HOLD_MS after the last
       character, then let the S-meter take the line back. */
    if (rx_show_ms > 0u)
        rx_show_ms = (rx_show_ms > 10u) ? (uint16_t)(rx_show_ms - 10u) : 0u;

    /* The receiver runs from SysTick at 1ms (CW_RX_Sample); here we only refresh the
       signal bar, at 20Hz -- blitting costs ~7.5ms at 1MHz SPI. */
    static uint8_t bar_refresh_ctr = 0;
    if (gScreenToDisplay == DISPLAY_CW_CHAT) {
        CW_RX_UpdateSMeter();                 /* peak-hold S-meter, 10ms cadence */
        if (++bar_refresh_ctr >= 5u) {
            bar_refresh_ctr = 0;
            gUpdateDisplay = true;
        }
    } else if (gScreenToDisplay == DISPLAY_MAIN && CW_MonScope() >= 1u) {
        /* Refresh the main screen while the CW line is showing, and once when it clears. */
        static bool    was_showing = false;
        static uint8_t main_ctr    = 0;
        bool showing = (rx_show_ms > 0u);
        if (showing) {
            if (++main_ctr >= 5u) { main_ctr = 0; gUpdateDisplay = true; }
        } else if (was_showing) {
            gUpdateDisplay = true;            /* CW just ended -> redraw so the S-meter returns */
        }
        was_showing = showing;
    }
}

void CW_TimeSlice500ms(void)
{
    if (gScreenToDisplay != DISPLAY_CW_CHAT) {
        /* Screen exited — flush any unsaved prediction weights */
        if (cw_pred_dirty) {
            cw_pred_save();
            cw_pred_dirty = false;
        }
        return;
    }

    /* Also flush weights at idle (covers popup use without transmitting) */
    if (cw_pred_dirty && tx_state == CW_TX_IDLE) {
        cw_pred_save();
        cw_pred_dirty = false;
    }

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
        gEeprom.CW_RX_THRESHOLD = rx_threshold;
        SETTINGS_SaveCwThreshold();
        break;

    default:
        break;
    }

    gRequestDisplayScreen = DISPLAY_CW_CHAT;
}

#endif /* ENABLE_FEAT_ELW_CW */
