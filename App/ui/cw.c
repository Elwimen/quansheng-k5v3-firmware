#ifdef ENABLE_FEAT_ELW_CW

#include <string.h>
#include "app/cw.h"
#include "driver/st7565.h"
#include "driver/bk4819.h"
#include "ui/helper.h"
#include "settings.h"
#include "radio.h"
#include "misc.h"
#include "external/printf/printf.h"

#define CW_HIST_Y0  12u   /* framebuffer-relative y of first history line */
#define CW_HIST_DY   7u   /* pixels per history line (gFont5x7)           */

void UI_DisplayCwChat(void)
{
    char buf[48];

    UI_DisplayClear();

    /* Line 0 — status: [*]mode + channel-name-or-frequency (left); S-meter + TX/RX speed
       (right). The '*' shows while a signal is being received; the S-meter is the strength
       of the station you're copying; TX/RX = your sending speed / the detected sender speed. */
    {
        const bool ook  = (gCurrentVfo->Modulation == MODULATION_CW);
        const bool rxng = (CW_RX_GetState() != 0u);          /* not idle => hearing something */

        /* Location: the memory-channel name when tuned to one, otherwise the frequency. */
        char loc[14];
        uint16_t ch = gEeprom.ScreenChannel[gEeprom.RX_VFO];
        if (IS_MR_CHANNEL(ch)) {
            char name[16];
            SETTINGS_FetchChannelName(name, ch);
            if (name[0]) {
                name[8] = '\0';                              /* fits beside the S-meter/speed */
                strcpy(loc, name);
            } else {
                sprintf_(loc, "CH%03u", ch + 1u);
            }
        } else {
            const uint32_t freq = gCurrentVfo->pRX->Frequency;
            sprintf_(loc, "%u.%03u", freq / 100000, (freq % 100000) / 100);
        }
        sprintf_(buf, "%s%s %s", rxng ? "*" : "", ook ? "CW" : "aCW", loc);
        UI_PrintStringSmallBold(buf, 0, 0, 0);

        /* IARU S-meter (peak-held so a keyed carrier doesn't strobe it). */
        uint8_t s = CW_RX_GetSLevel();

        /* Right side — S{level} {TXwpm}/{RXwpm}, inverted 3×5 font. RX shows '--' until the
           sender's speed is acquired. */
        uint8_t rxwpm = CW_RX_GetWpm();
        char indicator[16];
        if (rxwpm)
            sprintf_(indicator, "S%u %u/%u", s, gEeprom.CW_WPM, rxwpm);
        else
            sprintf_(indicator, "S%u %u/--", s, gEeprom.CW_WPM);
        uint8_t ind_w = (uint8_t)(strlen(indicator) * 4u);  /* 4 px per glyph */
        uint8_t ind_x = (uint8_t)(128u - ind_w);
        for (uint8_t px = ind_x - 1u; px < 128u; px++)     /* black background */
            gFrameBuffer[0][px] |= 0xFFu;
        GUI_DisplaySmallest(indicator, ind_x, 1, false, false); /* white glyphs */

        /* Rhythm scope — 3px strip (bits 0-2 of gFrameBuffer[1]) showing the last ~1s of
           the debounced on/off signal, oldest at the left, newest at the right. You can
           read the Morse rhythm off it directly. */
        uint8_t scope[CW_SCOPE_LEN];
        CW_RX_GetScope(scope);
        for (uint8_t x = 0; x < CW_SCOPE_LEN && x < 128u; x++)
            if (scope[x])
                gFrameBuffer[1][x] |= 0x07u;                        /* bits 0-2 */
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

    /* Live decode — the element being received right now, as dits/dahs, tacked onto the end
       of the newest RX line so you watch it build up and snap to a letter. */
    if (CW_RX_GetState() != 0u && cw_history_count > 0) {
        uint8_t last = (uint8_t)(cw_history_count - 1u);
        if (cw_history[last].tag == CW_MSG_RX &&
            last >= cw_scroll && last < (uint8_t)(cw_scroll + hist_lines)) {
            char pat[10];
            CW_RX_GetLivePattern(pat, sizeof(pat));
            if (pat[0]) {
                uint8_t ly = (uint8_t)(CW_HIST_Y0 + (last - cw_scroll) * CW_HIST_DY);
                uint8_t tx = (uint8_t)(6u + strlen(cw_history[last].text) * 6u + 3u);
                if (tx < 122u)
                    GUI_Display5x7(pat, tx, ly, true);
            }
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
        const uint8_t visible = 12;   /* 7px/char: ">"+12+"_" = 105px, clear of the counter */
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

            /* Right-aligned message capacity: chars used / total, 3×5 font. */
            char cnt[8];
            sprintf_(cnt, "%u/%u", dlen, (uint8_t)(CW_COMPOSE_MAX - 1u));
            uint8_t cnt_x = (uint8_t)(128u - (uint8_t)strlen(cnt) * 4u);
            GUI_DisplaySmallest(cnt, cnt_x, 49, false, true);
        }
    }

    ST7565_BlitFullScreen();
}

#endif /* ENABLE_FEAT_ELW_CW */
