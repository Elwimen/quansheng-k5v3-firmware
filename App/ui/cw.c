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
        sprintf_(buf, "CW %uW %u.%03u",
                 gEeprom.CW_WPM,
                 freq / 100000,
                 (freq % 100000) / 100);
        UI_PrintStringSmallBold(buf, 0, 0, 0);

        char counter[8];
        sprintf_(counter, "%u/64", (uint8_t)strlen(cw_compose));
        uint8_t ctr_x = (uint8_t)(128 - strlen(counter) * 7);
        UI_PrintStringSmallNormal(counter, ctr_x, 0, 0);
    }

    /* Lines 1–5 — message history (5 visible lines) */
    for (uint8_t i = 0; i < 5; i++) {
        uint8_t idx = cw_scroll + i;
        if (idx >= cw_history_count)
            break;
        if (cw_history[idx].tag == CW_MSG_TX) {
            UI_PrintStringSmallBold("TX", 0, 0, 1 + i);
            UI_PrintStringSmallNormal(cw_history[idx].text, 21, 0, 1 + i);
        } else if (cw_history[idx].tag == CW_MSG_RX) {
            UI_PrintStringSmallNormal("RX", 0, 0, 1 + i);
            UI_PrintStringSmallNormal(cw_history[idx].text, 21, 0, 1 + i);
        } else {
            /* CW_MSG_CONT — text already has leading space for indentation */
            UI_PrintStringSmallNormal(cw_history[idx].text, 0, 0, 1 + i);
        }
    }

    /* Scroll bar (x=126-127, y=8-47) — only when history exceeds visible lines */
    if (cw_history_count > CW_VISIBLE_LINES) {
        const uint8_t track_top = 8;
        const uint8_t track_h   = 40;  /* 5 lines × 8px */
        uint8_t thumb_h = (uint8_t)((uint16_t)track_h * CW_VISIBLE_LINES / cw_history_count);
        if (thumb_h < 4) thumb_h = 4;
        uint8_t thumb_y = track_top + (uint8_t)((uint16_t)(track_h - thumb_h) * cw_scroll
                          / (cw_history_count - CW_VISIBLE_LINES));
        /* track */
        UI_DrawLineBuffer(gFrameBuffer, 127, track_top, 127, track_top + track_h - 1, true);
        /* thumb */
        UI_DrawLineBuffer(gFrameBuffer, 126, thumb_y, 126, thumb_y + thumb_h - 1, true);
        UI_DrawLineBuffer(gFrameBuffer, 127, thumb_y, 127, thumb_y + thumb_h - 1, true);
    }

    /* Line 6 — compose line, full width, scrolling tail */
    {
        uint8_t dlen = (uint8_t)strlen(cw_compose);
        /* visible window: ">" + up to 16 chars + cursor = 18 chars × 7px = 126px */
        const uint8_t visible = 16;
        const char *tail = cw_compose + (dlen > visible ? dlen - visible : 0);
        char disp[visible + 3];
        uint8_t i = 0;
        disp[i++] = '>';
        while (*tail) disp[i++] = *tail++;
        disp[i++] = cw_cursor_visible ? '_' : ' ';
        disp[i]   = '\0';
        UI_PrintStringSmallNormal(disp, 0, 0, 6);
    }

    ST7565_BlitFullScreen();
}

#endif /* ENABLE_FEAT_ELW_CW */
