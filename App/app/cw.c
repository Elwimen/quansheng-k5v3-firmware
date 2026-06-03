#ifdef ENABLE_FEAT_ELW_CW

#include <string.h>
#include "app/cw.h"
#include "app/t9.h"
#include "settings.h"

/* ------------------------------------------------------------------ */
/* State                                                               */
/* ------------------------------------------------------------------ */

CwHistoryEntry_t cw_history[CW_HISTORY_LINES];
uint8_t          cw_history_count = 0;
uint8_t          cw_scroll        = 0;
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
/* Init / timeslices                                                   */
/* ------------------------------------------------------------------ */

void CW_Init(void)
{
    memset(cw_history, 0, sizeof(cw_history));
    cw_history_count  = 0;
    cw_scroll         = 0;
    cw_live_char      = '\0';
    cw_cursor_visible = true;
    T9_Init(&cw_t9, cw_compose, CW_COMPOSE_MAX, cw_char_map);
}

void CW_TimeSlice10ms(void)
{
    if (cw_t9.last_key != 255 && cw_t9_timeout > 0) {
        if (--cw_t9_timeout == 0) {
            T9_Commit(&cw_t9);
            gRequestDisplayScreen = DISPLAY_CW_CHAT;
        }
    }
    /* Phase 3: cw_tx_tick(); */
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
        T9_Key(&cw_t9, Key, bKeyHeld);
        cw_t9_timeout = CW_T9_TIMEOUT_TICKS;
        break;

    case KEY_STAR:
        T9_Backspace(&cw_t9);
        cw_t9_timeout = 0;
        break;

    case KEY_PTT:
        if (bKeyPressed && !bKeyHeld) {
            T9_Commit(&cw_t9);
            if (strlen(cw_compose) > 0) {
                cw_history_push(cw_compose, CW_MSG_TX);
                /* Phase 3: CW_TX_Start(cw_compose); */
                T9_Reset(&cw_t9);
            }
        }
        break;

    case KEY_MENU:
        if (!bKeyHeld)
            T9_Reset(&cw_t9);
        break;

    case KEY_UP:
        if (cw_scroll > 0) cw_scroll--;
        break;

    case KEY_DOWN:
        if (cw_scroll + CW_VISIBLE_LINES < cw_history_count) cw_scroll++;
        break;

    case KEY_EXIT:
        if (!bKeyHeld && cw_t9.last_key != 255) {
            /* commit the cycling char and keep composing */
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

bool CW_TX_Active(void)
{
    return false; /* Phase 3 will implement this */
}

#endif /* ENABLE_FEAT_ELW_CW */
