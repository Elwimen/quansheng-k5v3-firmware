#include <string.h>
#include "app/t9.h"

void T9_Init(T9State_t *s, char *buf, uint8_t buf_max,
             const char * const *char_map)
{
    s->buf      = buf;
    s->buf_max  = buf_max;
    s->char_map = char_map;
    T9_Reset(s);
}

void T9_Reset(T9State_t *s)
{
    s->buf[0]   = '\0';
    s->len      = 0;
    s->last_key = 255;
    s->char_idx = 0;
}

void T9_Commit(T9State_t *s)
{
    if (s->last_key == 255)
        return;
    s->len++;
    s->buf[s->len] = '\0';
    s->last_key    = 255;
    s->char_idx    = 0;
}

void T9_Key(T9State_t *s, KEY_Code_t key, bool held)
{
    if (s->len >= s->buf_max - 1)
        return;

    uint8_t kid = (uint8_t)(key - KEY_0);

    if (held) {
        if (s->last_key != 255) s->len++;
        s->buf[s->len++] = '0' + kid;
        s->buf[s->len]   = '\0';
        s->last_key      = 255;
        return;
    }

    if (key != s->last_key) {
        if (s->last_key != 255) s->len++;
        s->last_key = key;
        s->char_idx = 0;
    } else {
        s->char_idx++;
        if (s->char_map[kid][s->char_idx] == '\0')
            s->char_idx = 0;
    }

    if (s->len < s->buf_max - 1) {
        s->buf[s->len]     = s->char_map[kid][s->char_idx];
        s->buf[s->len + 1] = '\0';
    }
}

void T9_Backspace(T9State_t *s)
{
    if (s->last_key != 255) {
        s->buf[s->len] = '\0';
        s->last_key    = 255;
        s->char_idx    = 0;
    } else if (s->len > 0) {
        s->buf[--s->len] = '\0';
    }
}
