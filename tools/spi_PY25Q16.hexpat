#pragma description Quansheng UV-K5 V3 / UV-K1 PY25Q16 external SPI flash (settings, channels, calibration, boot logo)
#pragma endian little
#pragma pattern_limit 200000
#pragma array_limit 200000

// Layout mirrors firmware/App/driver/eeprom_compat.c (ADDR_MAPPINGS) and the
// PY25Q16_ReadBuffer/WriteBuffer offsets in firmware/App/settings.c.
//
//   0x000000  1024 channel frequency records  (16 B each)
//   0x004000  1024 channel names              (16 B each)
//   0x008000  1031 channel attributes         (2 B each: 1024 MR + 7 VFO)
//   0x00880E  24 scan-list names              (4 B each)
//   0x009000  7 band VFO pairs (A/B)          (32 B each)
//   0x00A000  settings block
//   0x010000  calibration (512 B)
//   0x011000  boot logo sector (4 KB)
//
// Everything else is erased flash (0xFF).

import std.core;
import std.string;

// ---------------------------------------------------------------- formatters

fn fmt_freq(u32 f) {
    if (f == 0xFFFFFFFF)
        return "<erased>";
    return std::format("{}.{:05} MHz", f / 100000, f % 100000);
};

fn fmt_offset(u32 f) {
    if (f == 0xFFFFFFFF)
        return "<erased>";
    return std::format("{}.{:05} MHz", f / 100000, f % 100000);
};

// Channel names / callsigns are 0xFF when never written.
fn fmt_name(str s) {
    return std::format("\"{}\"", s);
};

using Freq   = u32 [[format("fmt_freq"), color("4FC3F7")]];
using Offset = u32 [[format("fmt_offset"), color("29B6F6")]];

// -------------------------------------------------------------------- enums

enum CodeType : u8 {
    Off              = 0,
    CTCSS            = 1,
    DCS              = 2,
    DCS_Reversed     = 3,
};

enum Modulation : u8 {
    FM  = 0,
    AM  = 1,
    USB = 2,
    CW  = 3,
};

enum OffsetDir : u8 {
    Off  = 0,
    Plus = 1,
    Minus= 2,
};

enum TxPower : u8 {
    User = 0,
    Low1 = 1,
    Low2 = 2,
    Low3 = 3,
    Low4 = 4,
    Low5 = 5,
    Mid  = 6,
    High = 7,
};

enum DisplayMode : u8 {
    Frequency = 0,
    Channel   = 1,
    Name      = 2,
    NameFreq  = 3,
};

// ------------------------------------------------- 0x000000 channel records

// SETTINGS_SaveChannel() / SETTINGS_FetchChannelScanInfo()
bitfield ChannelFlags1 {
    reverse         : 1;    // FrequencyReverse
    bandwidth       : 1;    // 0 = wide, 1 = narrow
    tx_power        : 3;    // TxPower
    busy_lock       : 1;    // BUSY_CHANNEL_LOCK
    tx_lock         : 1;    // TX_LOCK
    unused          : 1;
} [[bitfield_order(std::core::BitfieldOrder::LeastToMostSignificant, 8)]];

bitfield ChannelFlags2 {
    dtmf_decode     : 1;    // DTMF_DECODING_ENABLE
    ptt_id          : 3;    // DTMF_PTT_ID_TX_MODE
    unused          : 4;
} [[bitfield_order(std::core::BitfieldOrder::LeastToMostSignificant, 8)]];

bitfield CodeTypes {
    rx_code_type    : 4;    // CodeType
    tx_code_type    : 4;    // CodeType
} [[bitfield_order(std::core::BitfieldOrder::LeastToMostSignificant, 8)]];

bitfield ModAndDir {
    offset_dir      : 4;    // OffsetDir
    modulation      : 4;    // Modulation
} [[bitfield_order(std::core::BitfieldOrder::LeastToMostSignificant, 8)]];

struct Channel {
    Freq       rx_frequency;
    Offset     tx_offset;
    u8         rx_code                      [[color("81C784")]];   // CTCSS/DCS index
    u8         tx_code                      [[color("81C784")]];
    CodeTypes  code_types                   [[color("66BB6A")]];
    ModAndDir  modulation_and_offset_dir    [[color("AED581")]];
    ChannelFlags1 flags1                    [[color("FFB74D")]];
    ChannelFlags2 flags2                    [[color("FFA726")]];
    u8         step_setting                 [[color("FF8A65")]];
    u8         scrambling                   [[color("FF7043")]];   // always 0 on F4HWN builds
} [[static, format("fmt_channel")]];

fn fmt_channel(Channel c) {
    if (c.rx_frequency == 0xFFFFFFFF)
        return "<empty>";
    return fmt_freq(c.rx_frequency);
};

// ---------------------------------------------------- 0x004000 channel names

struct ChannelName {
    char name[10]    [[color("BA68C8")]];   // displayed name
    char extra[6]    [[color("9575CD")]];   // second line / unused, usually 0xFF
} [[static]];

// ----------------------------------------------- 0x008000 channel attributes

// misc.h ChannelAttributes_t (packed into one u16, GCC LSB-first)
bitfield ChannelAttributes {
    band      : 3;      // 7 = channel free/unconfigured
    compander : 2;
    unused_1  : 1;
    unused_2  : 1;
    exclude   : 1;      // excluded from scan
    scanlist  : 8;      // scan-list membership bitmap
} [[bitfield_order(std::core::BitfieldOrder::LeastToMostSignificant, 16), color("F06292")]];

struct ScanListName {
    char name[4]     [[color("EC407A")]];
} [[static]];

// -------------------------------------------------------- 0x009000 band VFOs

// SETTINGS_SaveChannel(): VFO A at 0x009000 + band*32, VFO B at 0x009010 + band*32
struct VfoPair {
    Channel a       [[name("VFO A")]];
    Channel b       [[name("VFO B")]];
} [[static]];

// --------------------------------------------------------- 0x00A000 settings

bitfield AudioGain {                        // 0x00A000[0]
    fm : 4;
    am : 4;
} [[bitfield_order(std::core::BitfieldOrder::LeastToMostSignificant, 8)]];

bitfield LockBits {                         // 0x00A000[4]
    key_lock  : 1;
    menu_lock : 1;
    set_key   : 4;
    set_nav   : 1;
    unused    : 1;
} [[bitfield_order(std::core::BitfieldOrder::LeastToMostSignificant, 8)]];

struct Settings_A000 {
    AudioGain   audio_gain;
    u8          squelch_level;
    u8          tx_timeout_timer;
    u8          noaa_auto_scan;
    LockBits    lock_bits;
    u8          vox_switch;
    u8          vox_level;
    u8          mic_sensitivity;
} [[static, color("4DD0E1")]];

bitfield Backlight {                        // 0x00A008[0]
    max : 4;
    min : 4;
} [[bitfield_order(std::core::BitfieldOrder::LeastToMostSignificant, 8)]];

bitfield MiscBits_A00E {                    // 0x00A008[6]
    tail_tone_elimination : 1;
    narrower_fm           : 1;   // gSetting_set_nfm
    vfo_open              : 1;
    unused                : 5;
} [[bitfield_order(std::core::BitfieldOrder::LeastToMostSignificant, 8)]];

bitfield ResumeState {                      // 0x00A008[7]
    current_state : 3;
    current_list  : 5;
} [[bitfield_order(std::core::BitfieldOrder::LeastToMostSignificant, 8)]];

struct Settings_A008 {
    Backlight       backlight;
    DisplayMode     channel_display_mode;
    u8              cross_band_rx_tx;
    u8              battery_save;
    u8              dual_watch;
    u8              backlight_time;
    MiscBits_A00E   misc;
    ResumeState     resume_state;
} [[static, color("4DD0E1")]];

struct ChannelIndices {                     // 0x00A010, 8 x u16
    u16 screen_channel_a;
    u16 mr_channel_a;
    u16 freq_channel_a;
    u16 screen_channel_b;
    u16 mr_channel_b;
    u16 freq_channel_b;
    u16 noaa_channel_a;
    u16 noaa_channel_b;
} [[static, color("26C6DA")]];

bitfield FmFlags {
    is_mr_mode : 1;
    band       : 2;
    unused     : 5;
} [[bitfield_order(std::core::BitfieldOrder::LeastToMostSignificant, 8)]];

struct FmSettings {                         // 0x00A020
    u16     selected_frequency;
    u8      selected_channel;
    FmFlags flags;
    padding[4];
} [[static, color("7986CB")]];

struct FmChannels {                         // 0x00A028, FM_CHANNELS_MAX = 48
    u16 channel[48]  [[color("5C6BC0")]];
    padding[32];                            // 128 B reserved, only 96 used
} [[static]];

bitfield BeepAndKeyM {                      // 0x00A0A8[0]
    beep_control        : 1;
    key_m_long_press    : 7;
} [[bitfield_order(std::core::BitfieldOrder::LeastToMostSignificant, 8)]];

struct Settings_A0A8 {
    BeepAndKeyM beep_and_key_m;
    u8          key1_short;
    u8          key1_long;
    u8          key2_short;
    u8          key2_long;
    u8          scan_resume_mode;
    u8          auto_keypad_lock;
    u8          power_on_display_mode;
} [[static, color("4DD0E1")]];

struct PowerOnPassword {                    // 0x00A0B0
    u32 password    [[color("EF5350")]];
    padding[4];
} [[static]];

struct VoiceAndRssi {                       // 0x00A0B8
    u8   voice_prompt        [[color("4DD0E1")]];
    s8   dbm_corr_table[7]   [[color("26A69A")]];
} [[static]];

struct Settings_A0C0 {                      // 0x00A0C0
    u8 alarm_mode;
    u8 roger;
    u8 repeater_tail_tone_elimination;
    u8 tx_vfo;
    u8 battery_type;
    padding[3];
} [[static, color("4DD0E1")]];

struct LogoText {                           // 0x00A0C8, two 16-char lines
    char line1[16]  [[color("FFD54F")]];
    char line2[16]  [[color("FFCA28")]];
} [[static]];

struct DtmfTiming {                         // 0x00A0E8 (0x00A0A8 + 0x40)
    u8   side_tone;
    char separate_code;
    char group_call_code;
    u8   decode_response;
    u8   auto_reset_time;
    u8   preload_time_x10;
    u8   first_code_persist_x10;
    u8   hash_code_persist_x10;
    // 0x00A0F0
    u8   code_persist_x10;
    u8   code_interval_x10;
    u8   permit_remote_kill;
    padding[5];
} [[static, color("A1887F")]];

struct DtmfCodes {                          // 0x00A0F8, 56 B
    char ani_id[8]      [[color("8D6E63")]];
    char kill_code[8]   [[color("795548")]];
    char revive_code[8] [[color("8D6E63")]];
    char up_code[16]    [[color("795548")]];
    char down_code[16]  [[color("8D6E63")]];
} [[static]];

bitfield ScanListDefault {                  // 0x00A130[0]
    list_number : 7;
    enabled     : 1;
} [[bitfield_order(std::core::BitfieldOrder::LeastToMostSignificant, 8)]];

struct ScanListSettings {                   // 0x00A130
    ScanListDefault scan_list_default;
    u16             priority_ch_1;
    u16             priority_ch_2;
    u16             chan_1_call;
    padding[1];
} [[static, color("EC407A")]];

struct AesKey {                             // 0x00A138
    u32 key[4]  [[color("E57373")]];
} [[static]];

struct SpectrumSettings {                   // 0x00A148, 8 B (opaque)
    u8 raw[8]   [[color("90A4AE")]];
} [[static]];

bitfield MiscFlags_A157 {                   // 0x00A150[7]
    tx_enable          : 1;
    live_dtmf_decoder  : 1;
    battery_text       : 2;
    mic_bar            : 1;
    am_fix             : 1;
    backlight_on_tx_rx : 2;
} [[bitfield_order(std::core::BitfieldOrder::LeastToMostSignificant, 8)]];

struct Settings_A150 {                      // 0x00A150
    u8   f_lock;
    u8   tx_350;
    u8   killed;
    u8   tx_200;
    u8   tx_500;
    u8   enable_350;
    u8   scramble_enable;
    MiscFlags_A157 flags;
} [[static, color("4DD0E1")]];

bitfield SleepAndTimer {                    // 0x00A158[4]
    set_tmr : 1;
    set_off : 7;     // auto power-off, minutes
} [[bitfield_order(std::core::BitfieldOrder::LeastToMostSignificant, 8)]];

bitfield ContrastAndUi {                    // 0x00A158[5]
    set_ctr : 4;     // LCD contrast 1..15
    set_inv : 1;     // invert display
    set_lck : 1;     // PTT keylock
    set_met : 1;     // meter style
    set_gui : 1;     // GUI style
} [[bitfield_order(std::core::BitfieldOrder::LeastToMostSignificant, 8)]];

bitfield EotAndTot {                        // 0x00A158[6]
    set_eot : 4;
    set_tot : 4;
} [[bitfield_order(std::core::BitfieldOrder::LeastToMostSignificant, 8)]];

bitfield PttScanSave {                      // 0x00A158[7]
    set_ptt : 1;
    set_scn : 1;
    set_sav : 2;
    set_pwr : 4;
} [[bitfield_order(std::core::BitfieldOrder::LeastToMostSignificant, 8)]];

struct Settings_F4HWN {                     // 0x00A158
    padding[4];
    SleepAndTimer  sleep_and_timer;
    ContrastAndUi  contrast_and_ui;
    EotAndTot      eot_and_tot;
    PttScanSave    ptt_scan_save;
} [[static, color("FFB74D")]];

struct VersionString {                      // 0x00A160
    char version[16]    [[color("FFF176")]];
} [[static]];

// ELW CW-chat block (ENABLE_FEAT_ELW_CW), settings.c 0x00A170..0x00A190
struct CwSettings {
    u8      wpm                 [[color("4DB6AC")]];   // 5..40
    be u16  tone_hz             [[color("4DB6AC")]];   // 400..1000, big-endian
    u8      flags               [[color("26A69A")]];
    // 0x00A174
    u8      pred_counts[15]     [[color("80CBC4")]];   // prediction popup usage counters
    // 0x00A183
    char    callsign[12]        [[color("00897B")]];
    // 0x00A18F
    be u16  rx_threshold        [[color("4DB6AC")]];   // big-endian, 0xFFFF = auto
} [[static]];

// ------------------------------------------------------ 0x010000 calibration

// EEPROM 0x1E00..0x1FFF on the classic UV-K5 maps to flash 0x010000 here.
struct SquelchBand {                        // 6 tables x 16 entries (level 0..15)
    u8 open_rssi[16]     [[color("64B5F6")]];
    u8 close_rssi[16]    [[color("42A5F5")]];
    u8 open_noise[16]    [[color("2196F3")]];
    u8 close_noise[16]   [[color("1E88E5")]];
    u8 close_glitch[16]  [[color("1976D2")]];
    u8 open_glitch[16]   [[color("1565C0")]];
} [[static]];

struct RssiCalib {                          // 4 x u16 thresholds per band group
    u16 threshold[4]     [[color("9CCC65")]];
} [[static]];

struct TxPowerBand {                        // 0x0100D0 + band*16
    u8 low[3]            [[color("FFF59D")]];
    u8 mid[3]            [[color("FFF176")]];
    u8 high[3]           [[color("FFEE58")]];
    padding[7];
} [[static]];

struct Calibration {
    SquelchBand squelch_uhf  [[name("Squelch UHF (>= 174 MHz)")]];  // 0x010000, Base for BAND4+
    SquelchBand squelch_vhf  [[name("Squelch VHF (< 174 MHz)")]];   // 0x010060
    RssiCalib   rssi_band_4_7 [[name("RSSI calib bands 4-7")]];     // 0x0100C0
    RssiCalib   rssi_band_1_3 [[name("RSSI calib bands 1-3")]];     // 0x0100C8
    TxPowerBand tx_power[7]  [[name("TX power calib per band")]];   // 0x0100D0 .. 0x010140
    // 0x010140
    u16 battery_calibration[6]  [[color("EF5350")]];                // [3] drives voltage math
    // 0x01014C
    padding[0x150 - 0x14C];
    // 0x010150
    u16 vox1_threshold[12]      [[color("BA68C8")]];                // per VOX level
    // 0x010168
    u16 vox0_threshold[12]      [[color("AB47BC")]];
    // 0x010180
    padding[0x188 - 0x180];
    // 0x010188
    s16 bk4819_xtal_freq_low    [[color("FF7043")]];
    u16 eeprom_1F8A             [[color("FF8A65")]];
    u16 eeprom_1F8C             [[color("FF8A65")]];
    u8  volume_gain             [[color("FFAB91")]];
    u8  dac_gain                [[color("FFAB91")]];
} [[static]];

// -------------------------------------------------------- 0x011000 boot logo

struct BootLogo {
    u8 header[8]      [[color("BDBDBD")]];   // reserved (magic/version/flags)
    // 128x64 mono, ST7565-native: 8 pages x 128 columns, column-major, LSB = top
    u8 status_line[128] [[color("FFEB3B")]]; // page 0 -> gStatusLine
    u8 framebuffer[896] [[color("FFC107")]]; // pages 1..7 -> gFrameBuffer
} [[static]];

// ------------------------------------------------------------------ placement

Channel           channels[1024]        @ 0x000000 [[name("Channels (MR 0-1023)")]];
ChannelName       channel_names[1024]   @ 0x004000 [[name("Channel names")]];
ChannelAttributes attributes[1024 + 7]  @ 0x008000 [[name("Channel attributes (1024 MR + 7 VFO)")]];
ScanListName      scan_list_names[24]   @ 0x00880E [[name("Scan-list names")]];
VfoPair           vfos[7]               @ 0x009000 [[name("Band VFOs (A/B per band)")]];

Settings_A000     settings_basic        @ 0x00A000 [[name("Settings: squelch / TOT / VOX / mic")]];
Settings_A008     settings_display      @ 0x00A008 [[name("Settings: backlight / display / dual watch")]];
ChannelIndices    channel_indices       @ 0x00A010 [[name("Current channel indices")]];
FmSettings        fm_settings           @ 0x00A020 [[name("FM radio settings")]];
FmChannels        fm_channels           @ 0x00A028 [[name("FM radio channels")]];
Settings_A0A8     settings_keys         @ 0x00A0A8 [[name("Settings: keys / scan resume / power-on")]];
PowerOnPassword   power_on_password     @ 0x00A0B0 [[name("Power-on password")]];
VoiceAndRssi      voice_and_rssi        @ 0x00A0B8 [[name("Voice prompt + dBm correction table")]];
Settings_A0C0     settings_misc         @ 0x00A0C0 [[name("Settings: alarm / roger / battery type")]];
LogoText          logo_text             @ 0x00A0C8 [[name("Power-on logo text")]];
DtmfTiming        dtmf_timing           @ 0x00A0E8 [[name("DTMF timing")]];
DtmfCodes         dtmf_codes            @ 0x00A0F8 [[name("DTMF codes")]];
ScanListSettings  scan_list_settings    @ 0x00A130 [[name("Scan-list settings")]];
AesKey            aes_key               @ 0x00A138 [[name("AES key")]];
SpectrumSettings  spectrum_settings     @ 0x00A148 [[name("Spectrum settings")]];
Settings_A150     settings_flock        @ 0x00A150 [[name("Settings: F-LOCK / TX enables / flags")]];
Settings_F4HWN    settings_f4hwn        @ 0x00A158 [[name("Settings: F4HWN (PWR/PTT/contrast/sleep)")]];
VersionString     version               @ 0x00A160 [[name("Firmware version stamp")]];
CwSettings        cw_settings           @ 0x00A170 [[name("CW chat settings (ELW)")]];

Calibration       calibration           @ 0x010000 [[name("Calibration")]];
BootLogo          boot_logo             @ 0x011000 [[name("Boot logo bitmap")]];
