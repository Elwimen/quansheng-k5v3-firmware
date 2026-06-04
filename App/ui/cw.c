#ifdef ENABLE_FEAT_ELW_CW

#include <string.h>
#include "app/cw.h"
#include "driver/st7565.h"
#include "ui/helper.h"
#include "settings.h"
#include "radio.h"
#include "external/printf/printf.h"

#define CW_HIST_Y0  12u   /* framebuffer-relative y of first history line */
#define CW_HIST_DY   7u   /* pixels per history line (gFont5x7)           */

void UI_DisplayCwChat(void)
{
    char buf[48];

    UI_DisplayClear();

    /* Line 0 — status bar: WPM + frequency (left) + char counter (right) */
    {
        const uint32_t freq = gCurrentVfo->pRX->Frequency;
        const bool ook = (gCurrentVfo->Modulation == MODULATION_CW);
        sprintf_(buf, "%s %uW %u.%03u",
                 ook ? "CW" : "aCW",
                 gEeprom.CW_WPM,
                 freq / 100000,
                 (freq % 100000) / 100);
        UI_PrintStringSmallBold(buf, 0, 0, 0);

        /* Right side — {threshold}[chars_remaining], inverted 3×5 font */
        uint8_t remain = (uint8_t)((CW_COMPOSE_MAX - 1u) - strlen(cw_compose));
        char indicator[12];
        sprintf_(indicator, "%u %u", CW_RX_GetThreshold(), remain);
        uint8_t ind_w = (uint8_t)(strlen(indicator) * 4u);  /* 4 px per glyph */
        uint8_t ind_x = (uint8_t)(128u - ind_w);
        for (uint8_t px = ind_x - 1u; px < 128u; px++)     /* black background */
            gFrameBuffer[0][px] |= 0xFFu;
        GUI_DisplaySmallest(indicator, ind_x, 1, false, false); /* white glyphs */

        /* AF amplitude bar — 3px strip at the top of the history area (bits 0-2 of
           gFrameBuffer[1]), 1px below the status row. Drawn last so it separates
           status from history. Threshold shown as XOR notch. */
        uint8_t amp_v   = CW_RX_GetLastAmp();
        uint8_t thr_v   = (uint8_t)(CW_RX_GetThreshold() > 63u ? 63u : CW_RX_GetThreshold());
        uint8_t bar_end = (uint8_t)(amp_v * 2u);                    /* 0-126 px */
        uint8_t thr_x   = (uint8_t)(thr_v * 2u);
        for (uint8_t x = 0; x < bar_end && x < 128u; x++)
            gFrameBuffer[1][x] |= 0x07u;                            /* bits 0-2 */
        if (thr_x < 128u)
            gFrameBuffer[1][thr_x] ^= 0x07u;                        /* notch */
    }

    /* History — gFont5x7, y=12 (just below AF bar+gap), 7px pitch
     * popup steals gFontSmall Line=5 (y=40-47) when active */
    uint8_t hist_lines = CW_PopupActive() ? 4u : (uint8_t)CW_VISIBLE_LINES;
    for (uint8_t i = 0; i < hist_lines; i++) {
        uint8_t idx = cw_scroll + i;
        if (idx >= cw_history_count)
            break;
        uint8_t ly = (uint8_t)(CW_HIST_Y0 + i * CW_HIST_DY);
        bool selected = (cw_tx_recall >= 0 && (uint8_t)cw_tx_recall == idx);

        if (cw_history[idx].tag == CW_MSG_TX) {
            if (selected) {
                /* Inverted prefix block (6×7) then white '>' glyph */
                for (uint8_t bx = 0u; bx < 6u; bx++)
                    for (uint8_t by = 0u; by < CW_HIST_DY; by++)
                        gFrameBuffer[(ly + by) / 8u][bx] |=
                            (uint8_t)(1u << ((ly + by) % 8u));
                GUI_Display5x7(">", 0, ly, false);
            } else {
                GUI_Display5x7(">", 0, ly, true);
            }
            GUI_Display5x7(cw_history[idx].text, 6, ly, true);
        } else if (cw_history[idx].tag == CW_MSG_RX) {
            GUI_Display5x7("<", 0, ly, true);
            GUI_Display5x7(cw_history[idx].text, 6, ly, true);
        } else {
            /* CW_MSG_CONT — indented continuation, no prefix */
            GUI_Display5x7(cw_history[idx].text, 0, ly, true);
        }
    }

    /* Popup row — gFontSmall at Line=5 (y=40-47), shown when popup active */
    if (CW_PopupActive()) {
        char popup_buf[16];
        sprintf_(popup_buf, "> %s", CW_PopupItemText(CW_PopupSel()));
        UI_PrintStringSmallNormalInverse(popup_buf, 0, 0, 5);
        /* position indicator: sel+1 / total, right-aligned in 3x5 font */
        char pos_buf[8];
        sprintf_(pos_buf, "%u/%u", CW_PopupSel() + 1u, CW_PopupEffectiveCount());
        uint8_t pos_x = (uint8_t)(127u - (uint8_t)strlen(pos_buf) * 4u);
        GUI_DisplaySmallest(pos_buf, pos_x, 41, false, false);
    }

    /* Scroll bar — track covers the gFont5x7 history area */
    if (cw_history_count > hist_lines) {
        const uint8_t track_top = CW_HIST_Y0;
        const uint8_t track_h   = (uint8_t)(hist_lines * CW_HIST_DY);
        uint8_t thumb_h = (uint8_t)((uint16_t)track_h * hist_lines / cw_history_count);
        if (thumb_h < 4u) thumb_h = 4u;
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
