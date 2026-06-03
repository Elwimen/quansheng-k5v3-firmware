#ifdef ENABLE_FEAT_ELW_CW

#include <string.h>
#include "app/cw.h"
#include "driver/st7565.h"
#include "ui/helper.h"
#include "settings.h"
#include "radio.h"
#include "external/printf/printf.h"

void UI_DisplayCwChat(void)
{
    char buf[48];

    UI_DisplayClear();

    /* Line 0 — status bar: WPM + frequency (left) + char counter (right) */
    {
        const uint32_t freq = gCurrentVfo->pRX->Frequency;
        const bool ook = (gCurrentVfo->Modulation == MODULATION_CW);
        sprintf_(buf, "%s %uW %u.%03u",
                 ook ? "CW" : "AFCW",
                 gEeprom.CW_WPM,
                 freq / 100000,
                 (freq % 100000) / 100);
        UI_PrintStringSmallBold(buf, 0, 0, 0);

        /* Remaining chars — countdown, right-aligned, inverted 3×5 font */
        uint8_t remain = (uint8_t)((CW_COMPOSE_MAX - 1u) - strlen(cw_compose));
        char counter[4];
        sprintf_(counter, "%u", remain);
        uint8_t ctr_w = (uint8_t)(strlen(counter) * 4u);  /* 4 px per glyph */
        uint8_t ctr_x = (uint8_t)(127u - ctr_w);          /* 1 px right margin */
        for (uint8_t px = ctr_x - 1u; px < 128u; px++)    /* black background */
            gFrameBuffer[0][px] |= 0xFFu;
        GUI_DisplaySmallest(counter, ctr_x, 1, false, false); /* white glyphs */
    }

    /* Lines 1–(4 or 5) — message history; popup steals line 5 when active */
    uint8_t hist_lines = CW_PopupActive() ? 4u : (uint8_t)CW_VISIBLE_LINES;
    for (uint8_t i = 0; i < hist_lines; i++) {
        uint8_t idx = cw_scroll + i;
        if (idx >= cw_history_count)
            break;
        bool selected = (cw_tx_recall >= 0 && (uint8_t)cw_tx_recall == idx);
        if (cw_history[idx].tag == CW_MSG_TX) {
            UI_PrintStringSmallBold(selected ? ">>" : "TX", 0, 0, 1 + i);
            if (selected)
                UI_PrintStringSmallBold(cw_history[idx].text, 21, 0, 1 + i);
            else
                UI_PrintStringSmallNormal(cw_history[idx].text, 21, 0, 1 + i);
        } else if (cw_history[idx].tag == CW_MSG_RX) {
            UI_PrintStringSmallNormal("RX", 0, 0, 1 + i);
            UI_PrintStringSmallNormal(cw_history[idx].text, 21, 0, 1 + i);
        } else {
            /* CW_MSG_CONT — text already has leading space for indentation */
            UI_PrintStringSmallNormal(cw_history[idx].text, 0, 0, 1 + i);
        }
    }

    /* Popup row (line 5) — shown instead of 5th history line when popup active */
    if (CW_PopupActive()) {
        char popup_buf[12];
        sprintf_(popup_buf, "> %s", CW_PopupItemText(CW_PopupSel()));
        UI_PrintStringSmallNormalInverse(popup_buf, 0, 0, 5);
        uint8_t cnt = CW_PopupItemCount(CW_PopupSel());
        if (cnt > 0) {
            char cnt_buf[4];
            sprintf_(cnt_buf, "%u", cnt);
            uint8_t cnt_x = (uint8_t)(127u - (uint8_t)strlen(cnt_buf) * 4u);
            GUI_DisplaySmallest(cnt_buf, cnt_x, 41, false, false);
        }
    }

    /* Scroll bar — track covers the visible history area */
    if (cw_history_count > hist_lines) {
        const uint8_t track_top = 8;
        const uint8_t track_h   = (uint8_t)(hist_lines * 8u);
        uint8_t thumb_h = (uint8_t)((uint16_t)track_h * hist_lines / cw_history_count);
        if (thumb_h < 4) thumb_h = 4;
        uint8_t thumb_y = track_top + (uint8_t)((uint16_t)(track_h - thumb_h) * cw_scroll
                          / (cw_history_count - hist_lines));
        UI_DrawLineBuffer(gFrameBuffer, 127, track_top, 127, track_top + track_h - 1, true);
        UI_DrawLineBuffer(gFrameBuffer, 126, thumb_y, 126, thumb_y + thumb_h - 1, true);
        UI_DrawLineBuffer(gFrameBuffer, 127, thumb_y, 127, thumb_y + thumb_h - 1, true);
    }

    /* Line 6 — compose line (or recall preview when a TX entry is selected) */
    {
        const uint8_t visible = 16;
        char disp[visible + 3];
        uint8_t i = 0;

        if (cw_tx_recall >= 0) {
            /* Show recalled text with '*' prefix — bold to signal re-send mode */
            char recall[CW_COMPOSE_MAX];
            CW_RecallText(recall, sizeof(recall));
            uint8_t dlen = (uint8_t)strlen(recall);
            const char *tail = recall + (dlen > visible ? dlen - visible : 0);
            disp[i++] = '*';
            while (*tail) disp[i++] = *tail++;
            disp[i] = '\0';
            UI_PrintStringSmallBold(disp, 0, 0, 6);
        } else {
            /* Normal compose line: ">" + text + blinking cursor */
            uint8_t dlen = (uint8_t)strlen(cw_compose);
            const char *tail = cw_compose + (dlen > visible ? dlen - visible : 0);
            disp[i++] = '>';
            while (*tail) disp[i++] = *tail++;
            disp[i++] = cw_cursor_visible ? '_' : ' ';
            disp[i]   = '\0';
            UI_PrintStringSmallNormal(disp, 0, 0, 6);
        }
    }

    ST7565_BlitFullScreen();
}

#endif /* ENABLE_FEAT_ELW_CW */
