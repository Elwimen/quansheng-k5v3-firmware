#pragma description Quansheng UV-K5 V3 PY25Q16 SPI flash (generated from App/driver/spi_flash_layout.h -- DO NOT EDIT)
#pragma endian little
#pragma pattern_limit 2000000
#pragma array_limit 2000000

bitfield FL_Channel_bits0A {
    rxcodeflag : 4;
    txcodeflag : 4;
};

bitfield FL_Channel_bits0B {
    offsetDir : 4;
    modulation : 4;
};

bitfield FL_Channel_bits0C {
    freq_reverse : 1;
    bandwidth : 1;
    txpower : 3;
    busyChLockout : 1;
    txLock : 1;
    __UNUSED01 : 1;
};

bitfield FL_Channel_bits0D {
    dtmf_decode : 1;
    dtmf_pttid : 3;
    __UNUSED02 : 4;
};

struct FL_Channel {
    u32 freq;
    u32 offset;
    u8 rxcode;
    u8 txcode;
    FL_Channel_bits0A bits0A;
    FL_Channel_bits0B bits0B;
    FL_Channel_bits0C bits0C;
    FL_Channel_bits0D bits0D;
    u8 step;
    u8 __UNUSED03;
};

struct FL_ChannelName {
    char name[16];
};

bitfield FL_ChannelAttr_bits00 {
    band : 3;
    compander : 2;
    __UNUSED04 : 3;
};

struct FL_ChannelAttr {
    FL_ChannelAttr_bits00 bits00;
    u8 scanlist;
};

struct FL_ScanListName {
    char name[4];
};

bitfield FL_VfoChannel_bits0A {
    rxcodeflag : 4;
    txcodeflag : 4;
};

bitfield FL_VfoChannel_bits0B {
    offsetDir : 4;
    modulation : 4;
};

bitfield FL_VfoChannel_bits0C {
    freq_reverse : 1;
    bandwidth : 1;
    txpower : 3;
    busyChLockout : 1;
    txLock : 1;
    __UNUSED01 : 1;
};

bitfield FL_VfoChannel_bits0D {
    dtmf_decode : 1;
    dtmf_pttid : 3;
    __UNUSED02 : 4;
};

struct FL_VfoChannel {
    u32 freq;
    u32 offset;
    u8 rxcode;
    u8 txcode;
    FL_VfoChannel_bits0A bits0A;
    FL_VfoChannel_bits0B bits0B;
    FL_VfoChannel_bits0C bits0C;
    FL_VfoChannel_bits0D bits0D;
    u8 step;
    u8 __UNUSED03;
};

bitfield FL_SettingsA000_bits00 {
    set_rxa_fm : 4;
    set_rxa_am : 4;
};

bitfield FL_SettingsA000_bits04 {
    key_lock : 1;
    set_menu_lock : 1;
    set_key : 4;
    set_nav : 1;
    __UNUSED09 : 1;
};

struct FL_SettingsA000 {
    FL_SettingsA000_bits00 bits00;
    u8 squelch;
    u8 max_talk_time;
    u8 noaa_autoscan;
    FL_SettingsA000_bits04 bits04;
    u8 vox_switch;
    u8 vox_level;
    u8 mic_gain;
};

bitfield FL_SettingsA008_bits00 {
    backlight_max : 4;
    backlight_min : 4;
};

bitfield FL_SettingsA008_bits06 {
    ste : 1;
    set_nfm : 2;
    __UNUSED10 : 5;
};

struct FL_SettingsA008 {
    FL_SettingsA008_bits00 bits00;
    u8 channel_display_mode;
    u8 crossband;
    u8 battery_save;
    u8 dual_watch;
    u8 backlight_time;
    FL_SettingsA008_bits06 bits06;
    u8 current_state;
};

struct FL_ChannelIndices {
    u16 ScreenChannel_A;
    u16 MrChannel_A;
    u16 FreqChannel_A;
    u16 ScreenChannel_B;
    u16 MrChannel_B;
    u16 FreqChannel_B;
    u16 NoaaChannel_A;
    u16 NoaaChannel_B;
};

struct FL_FmChannels {
    u16 fmfreq[48];
};

bitfield FL_SettingsA0A8_bits00 {
    button_beep : 1;
    keyM_longpress_action : 7;
};

struct FL_SettingsA0A8 {
    FL_SettingsA0A8_bits00 bits00;
    u8 key1_shortpress_action;
    u8 key1_longpress_action;
    u8 key2_shortpress_action;
    u8 key2_longpress_action;
    u8 scan_resume_mode;
    u8 auto_keypad_lock;
    u8 power_on_dispmode;
    u32 password;
};

struct FL_VoiceAndRssi {
    u8 voice;
    s8 dbm_corr[7];
};

struct FL_SettingsA0C0 {
    u8 alarm_mode;
    u8 roger_beep;
    u8 rp_ste;
    u8 TX_VFO;
    u8 Battery_type;
};

struct FL_LogoText {
    char logo_line1[16];
    char logo_line2[16];
};

struct FL_Dtmf {
    u8 side_tone;
    char separate_code;
    char group_call_code;
    u8 decode_response;
    u8 auto_reset_time;
    u8 preload_time;
    u8 first_code_persist_time;
    u8 hash_persist_time;
    u8 code_persist_time;
    u8 code_interval_time;
    u8 permit_remote_kill;
    u8 __pad_A0F3[5];
    char local_code[3];
    char __pad_A0FB[5];
    char kill_code[5];
    char __pad_A103[3];
    char revive_code[5];
    char __pad_A10B[3];
    char up_code[16];
    char down_code[16];
};

bitfield FL_ScanListSettings_bits00 {
    slDef : 7;
    slPriorEnab : 1;
};

struct FL_ScanListSettings {
    FL_ScanListSettings_bits00 bits00;
    u16 slPriorCh1;
    u16 slPriorCh2;
    u16 call_channel;
    u8 __UNUSED11;
};

bitfield FL_SettingsA150_bits07 {
    __UNUSED12 : 1;
    live_DTMF_decoder : 1;
    battery_text : 2;
    mic_bar : 1;
    AM_fix : 1;
    backlight_on_TX_RX : 2;
};

struct FL_SettingsA150 {
    u8 int_flock;
    u8 int_350tx_unsused;
    u8 int_KILLED;
    u8 int_200tx_unsused;
    u8 int_500tx_unsused;
    u8 int_350en;
    u8 int_scren;
    FL_SettingsA150_bits07 bits07;
};

bitfield FL_BuildOptions_bits00 {
    ENABLE_FMRADIO : 1;
    ENABLE_NOAA : 1;
    ENABLE_VOICE : 1;
    ENABLE_VOX : 1;
    ENABLE_ALARM : 1;
    ENABLE_TX1750 : 1;
    ENABLE_PWRON_PASSWORD : 1;
    ENABLE_DTMF_CALLING : 1;
};

bitfield FL_BuildOptions_bits01 {
    ENABLE_FLASHLIGHT : 1;
    ENABLE_WIDE_RX : 1;
    ENABLE_RAW_DEMODULATORS : 1;
    ENABLE_FEAT_F4HWN_GAME : 1;
    ENABLE_AM_FIX : 1;
    ENABLE_BANDSCOPE : 1;
    ENABLE_FEAT_F4HWN_RESCUE_OPS : 1;
    __UNUSED13 : 1;
};

struct FL_BuildOptions {
    FL_BuildOptions_bits00 bits00;
    FL_BuildOptions_bits01 bits01;
};

bitfield FL_SettingsA15A_bits02 {
    set_tmr : 1;
    set_off_tmr : 7;
};

bitfield FL_SettingsA15A_bits03 {
    set_contrast : 4;
    set_inv : 1;
    set_lck : 1;
    set_met : 1;
    set_gui : 1;
};

bitfield FL_SettingsA15A_bits04 {
    set_eot : 4;
    set_tot : 4;
};

bitfield FL_SettingsA15A_bits05 {
    set_ptt : 1;
    set_scn : 1;
    __UNUSED16 : 2;
    set_pwr : 4;
};

struct FL_SettingsA15A {
    u8 __UNUSED14;
    u8 __UNUSED15;
    FL_SettingsA15A_bits02 bits02;
    FL_SettingsA15A_bits03 bits03;
    FL_SettingsA15A_bits04 bits04;
    FL_SettingsA15A_bits05 bits05;
};

struct FL_Version {
    char version[16];
};

struct FL_CwSettings {
    u8 cw_wpm;
    u8 cw_tone_hi;
    u8 cw_tone_lo;
    u8 cw_flags;
    u8 cw_pred_counts[15];
    char cw_callsign[12];
    u8 cw_thr_hi;
    u8 cw_thr_lo;
};

struct FL_Calibration_sqlBand4_7 {
    u8 openRssiThr[10];
    char __p0[6];
    u8 closeRssiThr[10];
    char __p1[6];
    u8 openNoiseThr[10];
    char __p2[6];
    u8 closeNoiseThr[10];
    char __p3[6];
    u8 closeGlitchThr[10];
    char __p4[6];
    u8 openGlitchThr[10];
    char __p5[6];
};

struct FL_Calibration_sqlBand1_3 {
    u8 openRssiThr[10];
    char __p0[6];
    u8 closeRssiThr[10];
    char __p1[6];
    u8 openNoiseThr[10];
    char __p2[6];
    u8 closeNoiseThr[10];
    char __p3[6];
    u8 closeGlitchThr[10];
    char __p4[6];
    u8 openGlitchThr[10];
    char __p5[6];
};

struct FL_Calibration_rssiLevelsBands3_7 {
    u16 level1;
    u16 level2;
    u16 level4;
    u16 level6;
};

struct FL_Calibration_rssiLevelsBands1_2 {
    u16 level1;
    u16 level2;
    u16 level4;
    u16 level6;
};

struct FL_Calibration_txp_low {
    u8 lower;
    u8 center;
    u8 upper;
};

struct FL_Calibration_txp_mid {
    u8 lower;
    u8 center;
    u8 upper;
};

struct FL_Calibration_txp_hi {
    u8 lower;
    u8 center;
    u8 upper;
};

struct FL_Calibration_txp {
    FL_Calibration_txp_low low;
    FL_Calibration_txp_mid mid;
    FL_Calibration_txp_hi hi;
    char __pad[7];
};

struct FL_Calibration {
    FL_Calibration_sqlBand4_7 sqlBand4_7;
    FL_Calibration_sqlBand1_3 sqlBand1_3;
    FL_Calibration_rssiLevelsBands3_7 rssiLevelsBands3_7;
    FL_Calibration_rssiLevelsBands1_2 rssiLevelsBands1_2;
    FL_Calibration_txp txp[7];
    u16 batLvl[6];
    char __pad_B14C[4];
    u16 vox1Thr[10];
    char __pad_B164[4];
    u16 vox0Thr[10];
    char __pad_B17C[4];
    u8 micLevel[5];
    char __pad_B185[3];
    s16 xtalFreqLow;
    char __pad_B18A[4];
    u8 volumeGain;
    u8 dacGain;
};

struct FL_BootLogo {
    u8 header[8];
    u8 status_line[128];
    u8 framebuffer[896];
};

// ------------------------------ placement
FL_Channel channels[1024] @ 0x000000 [[name("channels")]];
FL_ChannelName channel_names[1024] @ 0x004000 [[name("channel_names")]];
FL_ChannelAttr ch_attr[1031] @ 0x008000 [[name("ch_attr")]];
FL_ScanListName scan_list_names[24] @ 0x00880e [[name("scan_list_names")]];
FL_VfoChannel vfo_channels[14] @ 0x009000 [[name("vfo_channels")]];
FL_SettingsA000 settings_a000 @ 0x00a000 [[name("settings_a000")]];
FL_SettingsA008 settings_a008 @ 0x00a008 [[name("settings_a008")]];
FL_ChannelIndices channel_indices @ 0x00a010 [[name("channel_indices")]];
FL_FmChannels fm_channels @ 0x00a028 [[name("fm_channels")]];
FL_SettingsA0A8 settings_a0a8 @ 0x00a0a8 [[name("settings_a0a8")]];
FL_VoiceAndRssi voice_and_rssi @ 0x00a0b8 [[name("voice_and_rssi")]];
FL_SettingsA0C0 settings_a0c0 @ 0x00a0c0 [[name("settings_a0c0")]];
FL_LogoText logo_text @ 0x00a0c8 [[name("logo_text")]];
FL_Dtmf dtmf @ 0x00a0e8 [[name("dtmf")]];
FL_ScanListSettings scan_list @ 0x00a130 [[name("scan_list")]];
FL_SettingsA150 settings_a150 @ 0x00a150 [[name("settings_a150")]];
FL_BuildOptions build_options @ 0x00a158 [[name("build_options")]];
FL_SettingsA15A settings_a15a @ 0x00a15a [[name("settings_a15a")]];
FL_Version version @ 0x00a160 [[name("version")]];
FL_CwSettings cw @ 0x00a170 [[name("cw")]];
FL_Calibration calibration @ 0x00b000 [[name("calibration")]];
FL_BootLogo boot_logo @ 0x00c000 [[name("boot_logo")]];
