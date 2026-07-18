#pragma description UV-K5/K1 calibration block (SPI flash 0xB000)
// Generated from App/driver/spi_flash_layout.h by tools/gen_flash_layout.py.
// Load against a 512-byte calib.dat (uvflash dump-calib), NOT the full image.

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

FL_Calibration cal @ 0x00;
