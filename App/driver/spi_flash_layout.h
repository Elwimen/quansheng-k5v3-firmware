/* spi_flash_layout.h — SINGLE SOURCE OF TRUTH for the PY25Q16 external SPI-flash layout.
 *
 * The firmware compiles and uses this header (so if an offset/struct here is wrong, the
 * firmware breaks — it is self-validating), and tools/gen_flash_layout.py derives:
 *   - the ImHex pattern  tools/spi_PY25Q16.hexpat
 *   - the CHIRP MEM_FORMAT block for chirp/drivers/f4hwn_fusion.py
 * from the DWARF the compiler emits for it, so all three can never drift apart.
 *
 * Conventions
 * -----------
 *  - Addresses are RAW FLASH offsets (== what CHIRP's serial protocol and the flash image
 *    both use): calibration @0x0B000, boot logo @0x0C000. (eeprom_compat.c also keeps some
 *    legacy *logical* aliases like 0x010000->0x0B000 for the firmware's own classic-EEPROM
 *    accessors; those are internal and not part of this layout.)
 *  - Bitfields are declared LSB-first (GCC on little-endian): the first field occupies the
 *    least-significant bits. CHIRP's bitwise DSL is MSB-first, so the generator reverses the
 *    field order within each storage unit — both describe the same physical bits.
 *  - Field names match the CHIRP driver's get_memory() expectations so the generated
 *    MEM_FORMAT is drop-in.
 */

#ifndef SPI_FLASH_LAYOUT_H
#define SPI_FLASH_LAYOUT_H

#include <stdint.h>

/* ------------------------------------------------------------------ enums
 * (used to enrich the ImHex pattern; CHIRP treats these as plain u8) */

typedef enum __attribute__((packed)) {
    FL_MOD_FM  = 0,
    FL_MOD_AM  = 1,
    FL_MOD_USB = 2,
    FL_MOD_CW  = 3,
} FL_Modulation;

typedef enum __attribute__((packed)) {
    FL_OFFSET_NONE  = 0,
    FL_OFFSET_PLUS  = 1,
    FL_OFFSET_MINUS = 2,
} FL_OffsetDir;

typedef enum __attribute__((packed)) {
    FL_CODE_OFF          = 0,
    FL_CODE_CTCSS        = 1,
    FL_CODE_DCS          = 2,
    FL_CODE_DCS_REVERSED = 3,
} FL_CodeFlag;

/* ------------------------------------------------------ 0x000000 channels
 * SETTINGS_SaveChannel() / SETTINGS_FetchChannelScanInfo(). 16 bytes each. */

typedef struct __attribute__((packed)) {
    uint32_t freq;                 /* 0x00 : 10 Hz units */
    uint32_t offset;               /* 0x04 : repeater offset, 10 Hz units */
    uint8_t  rxcode;               /* 0x08 */
    uint8_t  txcode;               /* 0x09 */
    /* 0x0A */
    uint8_t  rxcodeflag : 4,
             txcodeflag : 4;
    /* 0x0B */
    uint8_t  offsetDir  : 4,
             modulation : 4;
    /* 0x0C */
    uint8_t  freq_reverse   : 1,
             bandwidth      : 1,
             txpower        : 3,
             busyChLockout  : 1,
             txLock         : 1,
             __UNUSED01     : 1;
    /* 0x0D */
    uint8_t  dtmf_decode : 1,
             dtmf_pttid  : 3,
             __UNUSED02  : 4;
    uint8_t  step;                 /* 0x0E */
    uint8_t  __UNUSED03;           /* 0x0F */
} FL_Channel;

/* ------------------------------------------------- 0x004000 channel names */
typedef struct __attribute__((packed)) { char name[16]; } FL_ChannelName;

/* --------------------------------------------- 0x008000 channel attributes
 * misc.h ChannelAttributes_t, packed into 2 bytes. 1024 MR + 7 VFO. */
typedef struct __attribute__((packed)) {
    uint8_t band      : 3,
            compander : 2,
            __UNUSED04 : 3;
    uint8_t scanlist;
} FL_ChannelAttr;

/* ------------------------------------------------ 0x00880E scan-list names */
typedef struct __attribute__((packed)) { char name[4]; } FL_ScanListName;

/* ---------------------------------------------------- 0x009000 band VFOs
 * 7 bands x (VFO A, VFO B), each a full channel record. 14 total. */
typedef FL_Channel FL_VfoChannel;

/* ------------------------------------------------- 0x00A000 settings blocks
 * Each 8- or 16-byte block matches a PY25Q16 access in settings.c. */

typedef struct __attribute__((packed)) {          /* 0x00A000 */
    uint8_t set_rxa_fm : 4, set_rxa_am : 4;
    uint8_t squelch;
    uint8_t max_talk_time;
    uint8_t noaa_autoscan;
    uint8_t key_lock : 1, set_menu_lock : 1, set_key : 4, set_nav : 1, __UNUSED09 : 1;
    uint8_t vox_switch;
    uint8_t vox_level;
    uint8_t mic_gain;
} FL_SettingsA000;

typedef struct __attribute__((packed)) {          /* 0x00A008 */
    uint8_t backlight_max : 4, backlight_min : 4;
    uint8_t channel_display_mode;
    uint8_t crossband;
    uint8_t battery_save;
    uint8_t dual_watch;
    uint8_t backlight_time;
    uint8_t ste : 1, set_nfm : 2, __UNUSED10 : 5;
    uint8_t current_state;
} FL_SettingsA008;

typedef struct __attribute__((packed)) {          /* 0x00A010 */
    uint16_t ScreenChannel_A, MrChannel_A, FreqChannel_A;
    uint16_t ScreenChannel_B, MrChannel_B, FreqChannel_B;
    uint16_t NoaaChannel_A, NoaaChannel_B;
} FL_ChannelIndices;

typedef struct __attribute__((packed)) {          /* 0x00A028 : FM_CHANNELS_MAX = 48 */
    uint16_t fmfreq[48];
} FL_FmChannels;

typedef struct __attribute__((packed)) {          /* 0x00A0A8 */
    uint8_t  button_beep : 1, keyM_longpress_action : 7;
    uint8_t  key1_shortpress_action;
    uint8_t  key1_longpress_action;
    uint8_t  key2_shortpress_action;
    uint8_t  key2_longpress_action;
    uint8_t  scan_resume_mode;
    uint8_t  auto_keypad_lock;
    uint8_t  power_on_dispmode;
    uint32_t password;
} FL_SettingsA0A8;

typedef struct __attribute__((packed)) {          /* 0x00A0B8 */
    uint8_t voice;
    int8_t  dbm_corr[7];
} FL_VoiceAndRssi;

typedef struct __attribute__((packed)) {          /* 0x00A0C0 */
    uint8_t alarm_mode;
    uint8_t roger_beep;
    uint8_t rp_ste;
    uint8_t TX_VFO;
    uint8_t Battery_type;
} FL_SettingsA0C0;

typedef struct __attribute__((packed)) {          /* 0x00A0C8 */
    char logo_line1[16];
    char logo_line2[16];
} FL_LogoText;

typedef struct __attribute__((packed)) {          /* 0x00A0E8 (timing) + 0x00A0F8 (codes) */
    uint8_t side_tone;
    char    separate_code;
    char    group_call_code;
    uint8_t decode_response;
    uint8_t auto_reset_time;
    uint8_t preload_time;
    uint8_t first_code_persist_time;
    uint8_t hash_persist_time;
    uint8_t code_persist_time;
    uint8_t code_interval_time;
    uint8_t permit_remote_kill;
    uint8_t __pad_A0F3[0xA0F8 - 0xA0F3];
    char    local_code[3];
    char    __pad_A0FB[5];
    char    kill_code[5];
    char    __pad_A103[3];
    char    revive_code[5];
    char    __pad_A10B[3];
    char    up_code[16];
    char    down_code[16];
} FL_Dtmf;

typedef struct __attribute__((packed)) {          /* 0x00A130 */
    uint8_t  slDef : 7, slPriorEnab : 1;
    uint16_t slPriorCh1;
    uint16_t slPriorCh2;
    uint16_t call_channel;
    uint8_t  __UNUSED11;
} FL_ScanListSettings;

typedef struct __attribute__((packed)) {          /* 0x00A150 */
    uint8_t int_flock;
    uint8_t int_350tx_unsused;
    uint8_t int_KILLED;
    uint8_t int_200tx_unsused;
    uint8_t int_500tx_unsused;
    uint8_t int_350en;
    uint8_t int_scren;
    uint8_t __UNUSED12 : 1, live_DTMF_decoder : 1, battery_text : 2,
            mic_bar : 1, AM_fix : 1, backlight_on_TX_RX : 2;
} FL_SettingsA150;

typedef struct __attribute__((packed)) {          /* 0x00A158 : F4HWN */
    uint8_t ENABLE_FMRADIO : 1, ENABLE_NOAA : 1, ENABLE_VOICE : 1, ENABLE_VOX : 1,
            ENABLE_ALARM : 1, ENABLE_TX1750 : 1, ENABLE_PWRON_PASSWORD : 1,
            ENABLE_DTMF_CALLING : 1;
    uint8_t ENABLE_FLASHLIGHT : 1, ENABLE_WIDE_RX : 1, ENABLE_RAW_DEMODULATORS : 1,
            ENABLE_FEAT_F4HWN_GAME : 1, ENABLE_AM_FIX : 1, ENABLE_BANDSCOPE : 1,
            ENABLE_FEAT_F4HWN_RESCUE_OPS : 1, __UNUSED13 : 1;
    uint8_t __UNUSED14;
    uint8_t __UNUSED15;
    uint8_t set_tmr : 1, set_off_tmr : 7;
    uint8_t set_contrast : 4, set_inv : 1, set_lck : 1, set_met : 1, set_gui : 1;
    uint8_t set_eot : 4, set_tot : 4;
    uint8_t set_ptt : 1, set_scn : 1, __UNUSED16 : 2, set_pwr : 4;
} FL_SettingsF4HWN;

typedef struct __attribute__((packed)) { char version[16]; } FL_Version;  /* 0x00A160 */

typedef struct __attribute__((packed)) {          /* 0x00A170 : CW chat (ELW) */
    uint8_t cw_wpm;
    uint8_t cw_tone_hi;
    uint8_t cw_tone_lo;
    uint8_t cw_flags;
    uint8_t cw_pred_counts[15];
    char    cw_callsign[12];
    uint8_t cw_thr_hi;
    uint8_t cw_thr_lo;
} FL_CwSettings;

/* ---------------------------------------------------- 0x00B000 calibration */
typedef struct __attribute__((packed)) {          /* 0x60 bytes: 6 x 10-entry tables */
    uint8_t openRssiThr[10];   char __p0[6];
    uint8_t closeRssiThr[10];  char __p1[6];
    uint8_t openNoiseThr[10];  char __p2[6];
    uint8_t closeNoiseThr[10]; char __p3[6];
    uint8_t closeGlitchThr[10];char __p4[6];
    uint8_t openGlitchThr[10]; char __p5[6];
} FL_SquelchBand;

typedef struct __attribute__((packed)) {
    uint16_t level1, level2, level4, level6;
} FL_RssiLevels;

typedef struct __attribute__((packed)) {
    struct __attribute__((packed)) { uint8_t lower, center, upper; } low;
    struct __attribute__((packed)) { uint8_t lower, center, upper; } mid;
    struct __attribute__((packed)) { uint8_t lower, center, upper; } hi;
    char __pad[7];
} FL_TxPower;

typedef struct __attribute__((packed)) {          /* 0x00B000, total 0x190 */
    FL_SquelchBand sqlBand4_7;      /* 0x00B000 */
    FL_SquelchBand sqlBand1_3;      /* 0x00B060 */
    FL_RssiLevels  rssiLevelsBands3_7;  /* 0x00B0C0 */
    FL_RssiLevels  rssiLevelsBands1_2;  /* 0x00B0C8 */
    FL_TxPower     txp[7];          /* 0x00B0D0 */
    uint16_t       batLvl[6];       /* 0x00B140 : [3] drives voltage math */
    char           __pad_B14C[4];
    uint16_t       vox1Thr[10];     /* 0x00B150 */
    char           __pad_B164[4];
    uint16_t       vox0Thr[10];     /* 0x00B168 */
    char           __pad_B17C[4];
    uint8_t        micLevel[5];     /* 0x00B180 */
    char           __pad_B185[3];
    int16_t        xtalFreqLow;     /* 0x00B188 */
    char           __pad_B18A[4];
    uint8_t        volumeGain;      /* 0x00B18E */
    uint8_t        dacGain;         /* 0x00B18F */
} FL_Calibration;

/* ----------------------------------------------------- 0x00C000 boot logo
 * 8-byte header + 128x64 mono ST7565-native bitmap (page 0 -> gStatusLine,
 * pages 1..7 -> gFrameBuffer). Beyond CHIRP's MEM_SIZE; ImHex only. */
typedef struct __attribute__((packed)) {
    uint8_t header[8];
    uint8_t status_line[128];
    uint8_t framebuffer[896];
} FL_BootLogo;

/* ------------------------------------------------------ region placement
 * The single machine-readable table of what lives where, consumed by the
 * firmware (offsets) and by the generator (regex on the preprocessed macro).
 *   X(field_name, C_type, count, flash_addr, chirp_name, in_chirp)
 * in_chirp: 1 if CHIRP's MEM_FORMAT/MEM_SIZE covers it (<= 0x00B190), else 0. */
#define FLASH_REGIONS(X) \
    X(channels,        FL_Channel,          1024, 0x000000, channel,        1) \
    X(channel_names,   FL_ChannelName,      1024, 0x004000, channelname,    1) \
    X(ch_attr,         FL_ChannelAttr,      1031, 0x008000, ch_attr,        1) \
    X(scan_list_names, FL_ScanListName,       24, 0x00880E, listname,       1) \
    X(vfo_channels,    FL_VfoChannel,         14, 0x009000, vfo_channel,    1) \
    X(settings_a000,   FL_SettingsA000,        1, 0x00A000, /*bare*/,       1) \
    X(settings_a008,   FL_SettingsA008,        1, 0x00A008, /*bare*/,       1) \
    X(channel_indices, FL_ChannelIndices,      1, 0x00A010, /*bare*/,       1) \
    X(fm_channels,     FL_FmChannels,          1, 0x00A028, /*bare*/,       1) \
    X(settings_a0a8,   FL_SettingsA0A8,        1, 0x00A0A8, /*bare*/,       1) \
    X(voice_and_rssi,  FL_VoiceAndRssi,        1, 0x00A0B8, /*bare*/,       1) \
    X(settings_a0c0,   FL_SettingsA0C0,        1, 0x00A0C0, /*bare*/,       1) \
    X(logo_text,       FL_LogoText,            1, 0x00A0C8, /*bare*/,       1) \
    X(dtmf,            FL_Dtmf,                1, 0x00A0E8, dtmf,           1) \
    X(scan_list,       FL_ScanListSettings,    1, 0x00A130, sl,             1) \
    X(settings_a150,   FL_SettingsA150,        1, 0x00A150, /*bare*/,       1) \
    X(build_options,   FL_SettingsF4HWN,       1, 0x00A158, BUILD_OPTIONS,  1) \
    X(version,         FL_Version,             1, 0x00A160, version,        1) \
    X(cw,              FL_CwSettings,          1, 0x00A170, cw,             1) \
    X(calibration,     FL_Calibration,         1, 0x00B000, cal,            1) \
    X(boot_logo,       FL_BootLogo,            1, 0x00C000, boot_logo,      0)

/* Firmware convenience: FL_ADDR_channels, FL_ADDR_calibration, ... */
#define _FL_ADDR(name, type, count, addr, cname, incp) \
    enum { FL_ADDR_##name = (addr) };
FLASH_REGIONS(_FL_ADDR)
#undef _FL_ADDR

#endif /* SPI_FLASH_LAYOUT_H */
