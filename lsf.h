// LS Formula: a tiny, bounded, integer-only per-pixel formula language.
//
// Pure C++11, no dependency on WLED or Arduino, so the same code is unit
// tested on the desktop and linked into the WLED usermod. No heap use, no
// floating point, no loops: a formula is compiled to straight-line bytecode
// whose run time is bounded by LSF_MAX_INSNS.
#pragma once
#include <stddef.h>
#include <stdint.h>

#ifndef LSF_MAX_SRC
#define LSF_MAX_SRC 1024  // bytes of source
#endif
#ifndef LSF_MAX_INSNS
#define LSF_MAX_INSNS 256   // bytecode instructions (LsfProgram::n is 16 bits)
#endif
#ifndef LSF_MAX_LOCALS
#define LSF_MAX_LOCALS 16    // `let` variables
#endif
#ifndef LSF_MAX_STACK
#define LSF_MAX_STACK 16    // evaluation stack depth
#endif
#define LSF_MAX_NAME    12    // characters in a `let` name

// Read-only inputs, in the order of the `in` array of LsfCtx.
enum LsfInput {
  LSF_IN_X = 0,  // 0..255 position across the segment (0 on a 1-pixel segment)
  LSF_IN_Y,      // 0..255 position down the segment (0 on 1D)
  LSF_IN_I,      // pixel index (2D: row * width + column)
  LSF_IN_N,      // pixel count
  LSF_IN_T,      // milliseconds (wraps at 2^32)
  LSF_IN_SP,     // WLED speed slider 0..255
  LSF_IN_IX,     // WLED intensity slider 0..255
  LSF_IN_C1,     // custom slider 1, 0..255
  LSF_IN_C2,     // custom slider 2
  LSF_IN_C3,     // custom slider 3 (0..31 in stock WLED)
  LSF_IN_W,      // segment width (1D: the length)
  LSF_IN_H,      // segment height (1D: 1)
  LSF_IN_K0,     // segment colour 1, packed 0xRRGGBB
  LSF_IN_K1,     // segment colour 2
  LSF_IN_K2,     // segment colour 3
  LSF_IN_CX,     // column (1D: same as i)
  LSF_IN_CY,     // row (1D: 0)
  LSF_NIN
};

enum LsfOp : uint8_t {
  OP_CONST, OP_IN, OP_LOAD, OP_STORE,
  OP_ADD, OP_SUB, OP_MUL, OP_DIV, OP_MOD, OP_SHL, OP_SHR,
  OP_AND, OP_OR, OP_XOR,
  OP_LT, OP_GT, OP_LE, OP_GE, OP_EQ, OP_NE, OP_LAND, OP_LOR,
  OP_NEG, OP_LNOT, OP_BNOT,
  OP_CALL,
  OP__COUNT
};

enum LsfFn : uint8_t {
  FN_SIN, FN_TRI, FN_NOISE, FN_HASH, FN_SCALE, FN_MIN, FN_MAX, FN_ABS,
  FN_CLAMP, FN_MIX, FN_SEL, FN_HSV, FN_RGB, FN_PAL, FN_R, FN_G, FN_B,
  FN_DIM, FN_BLEND, FN_PREV, FN_PREV2,
  FN__COUNT
};

struct LsfInsn {
  uint8_t op;
  uint8_t pad[3];
  int32_t imm;  // CONST value; IN/LOAD/STORE index; CALL function id
};

struct LsfProgram {
  LsfInsn  code[LSF_MAX_INSNS];
  uint16_t n;          // instruction count (may exceed 255)
  uint8_t  nlocals;
  uint8_t  maxstack;
  bool     usesPrev;   // calls prev()/prev2(): the host must keep a previous frame
  uint16_t cost;       // weighted per-pixel cost estimate (1 = one simple op)
};

struct LsfError {
  char     msg[40];
  uint16_t pos;        // byte offset into the source
  uint16_t line;       // 1-based
};

struct LsfCtx {
  int32_t in[LSF_NIN];
  void*   user;
  // Host callbacks. Both may be null (then the builtins return 0).
  uint32_t (*pal)(void* user, uint8_t idx);                    // palette sample, packed RGB
  uint32_t (*prev1)(void* user, int32_t index);                // previous frame, 0 if out of range
  uint32_t (*prev2)(void* user, int32_t col, int32_t row);     // previous frame in 2D
};

// Compile `src` (len bytes, need not be NUL-terminated). Returns false and
// fills *err on any error; *out is then unspecified.
bool lsf_compile(const char* src, size_t len, LsfProgram* out, LsfError* err);

// Independent check of a program (used after compile and for any program that
// came from elsewhere): valid opcodes and indices, no stack underflow or
// overflow, exactly one result left. Returns nullptr when valid, else a reason.
const char* lsf_verify(const LsfProgram& p);

// Run a verified program for one pixel. Result is packed 0xRRGGBB.
uint32_t lsf_run(const LsfProgram& p, const LsfCtx& ctx);

// Admission control. A formula's run time is cost x pixels, and that, not the
// instruction count, is what decides whether a strip can keep up. The estimate
// is per-pixel microseconds = cost * 0.3 + 1, calibrated on an ESP32-C6 at
// 160 MHz against eight real formulas (it came out 0.99x to 1.35x the measured
// worst frame; tests/test_lsf.cpp keeps it honest). Override LSF_US_PER_COST_X10 for a faster or slower chip.
#ifndef LSF_US_PER_COST_X10
#define LSF_US_PER_COST_X10 3
#endif
#ifndef LSF_US_PER_PIXEL_X10
#define LSF_US_PER_PIXEL_X10 10
#endif
// Estimated time in microseconds to render one frame of `pixels` pixels. Saturates at UINT32_MAX.
uint32_t lsf_estimate_us(const LsfProgram& p, uint32_t pixels);
// Largest pixel count whose estimated frame time fits in `budgetUs`.
uint32_t lsf_max_pixels(const LsfProgram& p, uint32_t budgetUs);

// Helpers exposed for the host and the tests.
uint8_t  lsf_sin8(uint8_t a);
uint32_t lsf_hash32(uint32_t x);
uint32_t lsf_hsv(int32_t h, int32_t s, int32_t v);
int      lsf_fn_arity(uint8_t fn);   // -1 if invalid
