// Host tests for the LS Formula compiler, verifier and interpreter.
// Build and run: wled/tests/run.sh
#include "../lsf.h"
#include <stdio.h>
#include <string.h>
#include <string>

static int g_fail = 0, g_run = 0;
#define CHECK(cond) do { g_run++; if (!(cond)) { g_fail++; printf("FAIL %s:%d: %s\n", __FILE__, __LINE__, #cond); } } while (0)

static LsfCtx baseCtx() {
  LsfCtx c; memset(&c, 0, sizeof(c));
  c.in[LSF_IN_N] = 100; c.in[LSF_IN_W] = 100; c.in[LSF_IN_H] = 1;
  return c;
}

static bool compile(const char* src, LsfProgram* p, LsfError* e = nullptr) {
  LsfError local;
  return lsf_compile(src, strlen(src), p, e ? e : &local);
}

// Evaluate (compile + run, no code execution) `expr` as the final expression and return its value (low 24 bits).
static uint32_t evalExpr(const char* expr, const LsfCtx* ctx = nullptr) {
  LsfProgram p; LsfError e;
  if (!compile(expr, &p, &e)) { printf("compile failed for '%s': %s @%u\n", expr, e.msg, e.pos); g_fail++; return 0xDEADBEEF; }
  LsfCtx c = ctx ? *ctx : baseCtx();
  return lsf_run(p, c);
}

static bool fails(const char* src, const char* msgPart, unsigned wantLine = 0) {
  LsfProgram p; LsfError e;
  if (compile(src, &p, &e)) { printf("expected failure: %s\n", src); return false; }
  if (msgPart && !strstr(e.msg, msgPart)) { printf("wrong error for '%s': '%s' (wanted '%s')\n", src, e.msg, msgPart); return false; }
  if (wantLine && e.line != wantLine) { printf("wrong line for '%s': %u\n", src, e.line); return false; }
  return true;
}

static void testArithmetic() {
  CHECK(evalExpr("1 + 2 * 3") == 7);
  CHECK(evalExpr("(1 + 2) * 3") == 9);
  CHECK(evalExpr("10 - 3 - 2") == 5);                  // left associative
  CHECK(evalExpr("100 / 7") == 14);
  CHECK(evalExpr("100 % 7") == 2);
  CHECK(evalExpr("5 / 0") == 0);                       // division by zero is defined
  CHECK(evalExpr("5 % 0") == 0);
  CHECK(evalExpr("0x10 + 0xff") == 271);
  CHECK(evalExpr("1 << 4") == 16);
  CHECK(evalExpr("256 >> 4") == 16);
  CHECK(evalExpr("1 << 33") == 2);                     // shift count masked to 5 bits
  CHECK(evalExpr("6 & 3") == 2);
  CHECK(evalExpr("6 | 3") == 7);
  CHECK(evalExpr("6 ^ 3") == 5);
  CHECK(evalExpr("3 < 4") == 1);
  CHECK(evalExpr("4 <= 4") == 1);
  CHECK(evalExpr("4 != 4") == 0);
  CHECK(evalExpr("1 && 0") == 0);
  CHECK(evalExpr("1 || 0") == 1);
  CHECK(evalExpr("!5") == 0);
  CHECK(evalExpr("-3 + 5") == 2);
  CHECK(evalExpr("1 | 2 == 2") == 1);                  // == binds tighter than |
  CHECK(evalExpr("0x7fffffff + 1 > 0") == 0);          // wraps, no UB
  CHECK(evalExpr("-0x7fffffff / -1 > 0") == 1);
  CHECK(evalExpr("2147483647 * 2 == -2") == 1);
}

static void testBuiltins() {
  CHECK(evalExpr("sin(0)") == 128);
  CHECK(evalExpr("sin(64)") == 255);
  CHECK(evalExpr("sin(192)") == 0);
  CHECK(evalExpr("sin(256)") == 128);                  // wraps
  CHECK(evalExpr("tri(0)") == 0);
  CHECK(evalExpr("tri(127)") == 254);
  CHECK(evalExpr("tri(255)") == 0);
  CHECK(evalExpr("scale(0, 10, 20)") == 10);
  CHECK(evalExpr("scale(255, 10, 20)") == 20);
  CHECK(evalExpr("scale(1000, 10, 20)") == 20);        // input clamped to 0..255
  CHECK(evalExpr("min(3, 4)") == 3);
  CHECK(evalExpr("max(3, 4)") == 4);
  CHECK(evalExpr("abs(0 - 9)") == 9);
  CHECK(evalExpr("clamp(300, 0, 255)") == 255);
  CHECK(evalExpr("clamp(5, 10, 0)") == 5);             // swapped bounds tolerated
  CHECK(evalExpr("mix(0, 100, 128)") == 50);
  CHECK(evalExpr("sel(1, 7, 8)") == 7);
  CHECK(evalExpr("sel(0, 7, 8)") == 8);
  CHECK(evalExpr("rgb(255, 0, 0)") == 0xFF0000);
  CHECK(evalExpr("rgb(999, -5, 3)") == 0xFF0003);
  CHECK(evalExpr("red(0x123456)") == 0x12);
  CHECK(evalExpr("green(0x123456)") == 0x34);
  CHECK(evalExpr("blue(0x123456)") == 0x56);
  CHECK(evalExpr("dim(0xFFFFFF, 128)") == 0x808080);
  CHECK(evalExpr("blend(0x000000, 0xFF0000, 255)") == 0xFF0000);
  CHECK(evalExpr("hsv(0, 255, 255)") == 0xFF0000);
  CHECK((evalExpr("hsv(85, 255, 255)") >> 8 & 0xFF) == 255);
  CHECK(evalExpr("hsv(0, 0, 200)") == 0xC8C8C8);       // zero saturation is grey
  CHECK(evalExpr("hsv(40, 255, 0)") == 0);
  CHECK(evalExpr("hash(1)") == evalExpr("hash(1)"));       // deterministic
  CHECK(evalExpr("hash(1)") != evalExpr("hash(2)"));
  CHECK(evalExpr("hash(1)") <= 0xFFFF);
}

static void testNoise() {
  // Value noise is in range, deterministic and continuous: neighbouring samples are close.
  int maxStep = 0, lo = 255, hi = 0;
  LsfProgram p; compile("noise(x, 12345)", &p);
  LsfCtx c = baseCtx();
  int prev = -1;
  for (int a = 0; a < 4096; a++) {
    c.in[LSF_IN_X] = a;
    int v = (int)lsf_run(p, c);
    CHECK(v >= 0 && v <= 255);
    if (v < lo) lo = v;
    if (v > hi) hi = v;
    if (prev >= 0) { int d = v > prev ? v - prev : prev - v; if (d > maxStep) maxStep = d; }
    prev = v;
  }
  CHECK(maxStep <= 8);       // 16 cells over 256 steps each: smooth
  CHECK(hi - lo > 100);      // actually varies
  // Negative coordinates floor consistently.
  CHECK(evalExpr("noise(0 - 1, 0 - 1)") <= 255);
}

static void testInputsAndLocals() {
  LsfCtx c = baseCtx();
  c.in[LSF_IN_X] = 77; c.in[LSF_IN_T] = 1000; c.in[LSF_IN_SP] = 9; c.in[LSF_IN_K1] = 0x00FF00;
  CHECK(evalExpr("x", &c) == 77);
  CHECK(evalExpr("t / 10", &c) == 100);
  CHECK(evalExpr("sp + ix", &c) == 9);
  CHECK(evalExpr("k1", &c) == 0x00FF00);
  CHECK(evalExpr("let a = x * 2\nlet b = a + 1\nb", &c) == 155);
  CHECK(evalExpr("let a = 3; let b = 4; a * b", &c) == 12);
  CHECK(evalExpr("# comment\nlet a = 3 # trailing\na + 1", &c) == 4);
  CHECK(evalExpr("\n\n  let a = 1\n\n  a  \n\n", &c) == 1);
}

static void testMultiline() {
  LsfCtx c = baseCtx(); c.in[LSF_IN_X] = 10;
  CHECK(evalExpr("sel(x > 5,\n  1,\n  2)", &c) == 1);                     // newlines inside a call
  CHECK(fails("(1 +\n 2) *\n 3", "expression expected"));                // the newline after `*` is outside the parens
  CHECK(evalExpr("let a = (1 +\n 2)\na * 4", &c) == 12);                  // inside grouping parens
  CHECK(evalExpr("min(1,\n max(2,\n  3))", &c) == 1);                     // nested
  CHECK(evalExpr("sel(\n# comment inside\n x > 5, 7, 8)", &c) == 7);       // comment lines inside parens
  CHECK(fails("1 +\n 2", "expression expected"));                          // outside parens a newline still ends the statement
  CHECK(fails("(1 +\n", "expression expected"));                           // unterminated paren at end of input
  CHECK(fails("sin(1\n2)", "',' or ')' expected"));
  CHECK(fails("1\n2", "final expression must be last", 2));                // unchanged
}

static void testRejects() {
  CHECK(fails("", "missing final"));
  CHECK(fails("let a = 1", "missing final"));
  CHECK(fails("1 +", "expression expected"));
  CHECK(fails("(1", "')' expected"));
  CHECK(fails("foo", "unknown name"));
  CHECK(fails("foo(1)", "unknown function"));
  CHECK(fails("sin(1, 2)", "wrong argument count"));
  CHECK(fails("sin()", "wrong argument count"));
  CHECK(fails("1.5", "integers only"));
  CHECK(fails("12abc", "integers only"));
  CHECK(fails("99999999999", "too large"));
  CHECK(fails("0x", "hex digits"));
  CHECK(fails("0x1FFFFFFFF", "too large"));
  CHECK(fails("let x = 1\nx", "already in use"));      // shadows an input
  CHECK(fails("let sin = 1\nsin", "already in use"));  // shadows a builtin
  CHECK(fails("let a = 1\nlet a = 2\na", "already in use"));
  CHECK(fails("let a = a\na", "unknown name"));         // no self reference
  CHECK(fails("1\n2", "final expression must be last", 2));
  CHECK(fails("1 2", "unexpected text"));
  CHECK(fails("let a = 1\na\nlet b = 2", "final expression must be last", 3));
  CHECK(fails("let = 1\n1", "name expected"));
  CHECK(fails("let a 1\n1", "'=' expected"));
  CHECK(fails("@", "unexpected character"));
  CHECK(fails("let averyveryverylongname = 1\n1", "name too long"));
  // Exactly LSF_MAX_LOCALS variables fit; one more is rejected.
  std::string fit, over;
  for (int k = 0; k < LSF_MAX_LOCALS; k++) fit += "let a" + std::to_string(k) + " = " + std::to_string(k) + "\n";
  over = fit + "let aover = 1\n";
  fit += "a0 + a" + std::to_string(LSF_MAX_LOCALS - 1);
  over += "a0";
  LsfProgram pf;
  CHECK(compile(fit.c_str(), &pf));
  CHECK(pf.nlocals == LSF_MAX_LOCALS);
  CHECK(fails(over.c_str(), "too many variables"));
}

static void testLimits() {
  // Instruction limit: "1+1+...+1" with k additions needs 2k+1 instructions.
  const int kOver = LSF_MAX_INSNS / 2 + 1, kFit = (LSF_MAX_INSNS - 1) / 2;
  std::string s = "1";
  for (int k = 0; k < kOver; k++) s += "+1";
  CHECK(fails(s.c_str(), "program too long"));
  s = "1";
  for (int k = 0; k < kFit; k++) s += "+1";
  LsfProgram p;
  CHECK(compile(s.c_str(), &p));
  CHECK(p.n == 2 * kFit + 1);
  // Source limit.
  std::string big(LSF_MAX_SRC + 1, ' ');
  CHECK(fails(big.c_str(), "source too long"));
  // Stack depth: right-nested expressions keep every left operand on the stack.
  std::string deep;
  for (int k = 0; k < 20; k++) deep += "1+(";
  deep += "1";
  for (int k = 0; k < 20; k++) deep += ")";
  CHECK(fails(deep.c_str(), "too deep"));
  // Parser recursion limit stops pathological nesting without overflowing the C++ stack.
  std::string parens(500, '(');
  parens += "1";
  parens += std::string(500, ')');
  CHECK(fails(parens.c_str(), "too deep"));
  std::string minus(500, '-');
  minus += "1";
  CHECK(fails(minus.c_str(), "too deep"));
  // NUL bytes are rejected.
  const char withNul[] = "1\0+2";
  LsfError e;
  CHECK(!lsf_compile(withNul, 4, &p, &e));
}

static void testVerifier() {
  LsfProgram p;
  CHECK(compile("let a = x\na + 1", &p));
  CHECK(lsf_verify(p) == nullptr);
  LsfProgram q = p; q.code[0].op = 200;               CHECK(lsf_verify(q) != nullptr);  // bad opcode
  q = p; q.code[0].imm = 99;                          CHECK(lsf_verify(q) != nullptr);  // bad input index
  q = p; q.n = 2;                                     CHECK(lsf_verify(q) != nullptr);  // IN, STORE: leaves no value
  q = p; q.n = 0;                                     CHECK(lsf_verify(q) != nullptr);
  q = p; q.code[1].imm = 5;                           CHECK(lsf_verify(q) != nullptr);  // bad local
  q = p; q.code[2].op = OP_ADD; q.n = 3;              CHECK(lsf_verify(q) != nullptr);  // underflow
  CHECK(compile("sin(x)", &p));
  q = p; q.code[1].imm = 300;                         CHECK(lsf_verify(q) != nullptr);  // bad fn id (would alias 44 if cast)
  q = p; q.code[1].imm = -1;                          CHECK(lsf_verify(q) != nullptr);
  CHECK(compile("1+1", &p));
  q = p; q.n = 2;                                     CHECK(lsf_verify(q) != nullptr);  // two values left
  // A hand-built local read before write must be rejected.
  LsfProgram r; memset(&r, 0, sizeof(r));
  r.n = 1; r.nlocals = 1; r.code[0].op = OP_LOAD; r.code[0].imm = 0;
  CHECK(lsf_verify(r) != nullptr);
}

// Fuzz: random byte strings and random token soup must never crash, and any
// accepted program must verify and run.
static uint32_t rng = 12345;
static uint32_t rnd() { rng = rng * 1664525u + 1013904223u; return rng >> 8; }

static void testFuzz() {
  static const char* toks[] = {"let ", "a", "b", "x", "t", " = ", "(", ")", ",", "+", "-", "*", "/", "%", "<<", ">>", "&", "|", "^",
                               "&&", "||", "!", "~", "<", ">=", "==", "sin", "noise", "hsv", "mix", "sel", "pal", "prev", "prev2",
                               "1", "255", "0x7fffffff", "\n", ";", "# c\n", "rgb", "dim", "blend", "clamp", "scale"};
  const int nt = sizeof(toks) / sizeof(toks[0]);
  int accepted = 0;
  for (int iter = 0; iter < 20000; iter++) {
    std::string s;
    int len = 1 + (int)(rnd() % 30);
    for (int k = 0; k < len; k++) s += toks[rnd() % nt];
    LsfProgram p; LsfError e;
    if (lsf_compile(s.data(), s.size(), &p, &e)) {
      accepted++;
      CHECK(lsf_verify(p) == nullptr);
      LsfCtx c = baseCtx();
      for (int k = 0; k < LSF_NIN; k++) c.in[k] = (int32_t)rnd() - 0x400000;
      (void)lsf_run(p, c);
    }
  }
  for (int iter = 0; iter < 20000; iter++) {
    char buf[64];
    int len = (int)(rnd() % 63) + 1;
    for (int k = 0; k < len; k++) buf[k] = (char)(1 + rnd() % 126);
    LsfProgram p; LsfError e;
    if (lsf_compile(buf, (size_t)len, &p, &e)) CHECK(lsf_verify(p) == nullptr);
  }
  CHECK(accepted > 100);  // the grammar-ish fuzz must reach the run path
}

// Callback behaviour: pal(), prev(), prev2().
static uint32_t palCb(void*, uint8_t idx) { return (uint32_t)idx << 8; }
static uint32_t prev1Cb(void*, int32_t i) { return i < 0 || i >= 10 ? 0 : (uint32_t)(i + 1); }
static uint32_t prev2Cb(void*, int32_t cx, int32_t cy) { return (cx < 0 || cy < 0) ? 0 : (uint32_t)(cx * 1000 + cy); }

static void testCallbacks() {
  LsfCtx c = baseCtx();
  c.pal = palCb; c.prev1 = prev1Cb; c.prev2 = prev2Cb;
  c.in[LSF_IN_I] = 4; c.in[LSF_IN_CX] = 3; c.in[LSF_IN_CY] = 2;
  CHECK(evalExpr("pal(5)", &c) == 0x500);
  CHECK(evalExpr("pal(256 + 5)", &c) == 0x500);
  CHECK(evalExpr("prev(0)", &c) == 5);
  CHECK(evalExpr("prev(1)", &c) == 6);
  CHECK(evalExpr("prev(0 - 5)", &c) == 0);
  CHECK(evalExpr("prev2(1, 1)", &c) == 4003);
  LsfProgram p; compile("prev(1)", &p);
  CHECK(p.usesPrev);
  compile("x", &p);
  CHECK(!p.usesPrev);
  // Null callbacks return 0.
  CHECK(evalExpr("pal(5)") == 0);
  CHECK(evalExpr("prev(0)") == 0);
}

static void testCost() {
  LsfProgram a, b;
  compile("x", &a);
  compile("noise(x, y) + noise(t, i)", &b);
  CHECK(b.cost > a.cost + 15);   // two noise() calls at weight 8 plus their operands
  CHECK(b.maxstack >= 2);
}

static void testEstimate() {
  LsfProgram p; compile("x", &p);                       // cost 1
  CHECK(p.cost == 1);
  // per pixel = cost * 0.3 + 1 us
  CHECK(lsf_estimate_us(p, 0) == 0);
  CHECK(lsf_estimate_us(p, 10) == 13);                  // 10 * 1.3 us
  LsfProgram q;
  compile("noise(x, y) + noise(t, i)", &q);             // heavier
  CHECK(q.cost > p.cost);
  CHECK(lsf_estimate_us(q, 100) > lsf_estimate_us(p, 100));
  CHECK(lsf_estimate_us(q, 200) >= 2 * lsf_estimate_us(q, 100) - 1);   // linear in pixels
  // max_pixels is the inverse: it fits the budget, one more pixel does not.
  for (uint32_t budget = 1000; budget <= 1000000; budget *= 10) {
    uint32_t m = lsf_max_pixels(q, budget);
    CHECK(lsf_estimate_us(q, m) <= budget);
    CHECK(lsf_estimate_us(q, m + 1) > budget);
  }
  // No overflow with absurd inputs.
  CHECK(lsf_estimate_us(q, 0xFFFFFFFFu) == 0xFFFFFFFFu);
  CHECK(lsf_max_pixels(q, 0xFFFFFFFFu) > 0);
  CHECK(lsf_max_pixels(q, 0) == 0);
  // The calibration points measured on the ESP32-C6 (50 LEDs, worst frame in us) stay within 0.9x..1.5x.
  struct { const char* file; unsigned measuredUs; } cal[] = {
    {"rainbow_wave", 461}, {"plasma", 674}, {"comet_trail", 668}, {"twinkle", 737}, {"ripple2d", 731}, {"fire", 1774}, {"falling_leaves", 2524}, {"bench_heavy_noise", 5913}};
  for (unsigned k = 0; k < sizeof(cal) / sizeof(cal[0]); k++) {
    std::string path = std::string(LSF_EXAMPLES_DIR) + "/" + cal[k].file + ".lsf";
    FILE* f = fopen(path.c_str(), "rb");
    if (!f) { g_fail++; continue; }
    char buf[LSF_MAX_SRC + 2]; size_t n = fread(buf, 1, sizeof(buf), f); fclose(f);
    LsfProgram e;
    if (!lsf_compile(buf, n, &e, nullptr)) { g_fail++; continue; }
    double ratio = (double)lsf_estimate_us(e, 50) / cal[k].measuredUs;
    if (ratio < 0.9 || ratio > 1.5) printf("calibration drift for %s: ratio %.2f\n", cal[k].file, ratio);
    CHECK(ratio >= 0.9 && ratio <= 1.5);
  }
}

static void testExamplesCompile() {
  // The shipped examples must all be valid.
  static const char* files[] = {"plasma", "rainbow_wave", "fire", "comet_trail", "twinkle", "ripple2d", "falling_leaves", "bench_heavy_noise"};
  for (unsigned k = 0; k < sizeof(files) / sizeof(files[0]); k++) {
    std::string path = std::string(LSF_EXAMPLES_DIR) + "/" + files[k] + ".lsf";
    FILE* f = fopen(path.c_str(), "rb");
    if (!f) { printf("missing example %s\n", path.c_str()); g_fail++; continue; }
    char buf[LSF_MAX_SRC + 2]; size_t n = fread(buf, 1, sizeof(buf), f); fclose(f);
    LsfProgram p; LsfError e;
    bool ok = lsf_compile(buf, n, &p, &e);
    if (!ok) printf("example %s: %s line %u\n", files[k], e.msg, e.line);
    CHECK(ok);
  }
}

int main() {
  testArithmetic(); testMultiline(); testBuiltins(); testNoise(); testInputsAndLocals(); testRejects();
  testLimits(); testVerifier(); testFuzz(); testCallbacks(); testCost(); testEstimate(); testExamplesCompile();
  printf("%d checks, %d failures\n", g_run, g_fail);
  return g_fail ? 1 : 0;
}
