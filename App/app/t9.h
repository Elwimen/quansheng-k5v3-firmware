#ifndef APP_T9_H
#define APP_T9_H

#include <stdint.h>
#include <stdbool.h>
#include "driver/keyboard.h"

typedef struct {
    char               *buf;
    uint8_t             buf_max;    /* capacity incl. NUL */
    const char * const *char_map;   /* 10-entry map for KEY_0..KEY_9 */
    KEY_Code_t          last_key;   /* 255 = no char pending */
    uint8_t             char_idx;
    uint8_t             len;        /* committed chars */
} T9State_t;

void T9_Init     (T9State_t *s, char *buf, uint8_t buf_max,
                  const char * const *char_map);
void T9_Key      (T9State_t *s, KEY_Code_t key, bool held);
void T9_Backspace(T9State_t *s);
void T9_Commit   (T9State_t *s);  /* commit pending char, advance len */
void T9_Reset    (T9State_t *s);  /* clear buffer and state */

#endif /* APP_T9_H */
