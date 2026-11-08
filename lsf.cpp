#include "lsf.h"
#include <string.h>

static_assert(LSF_MAX_INSNS >= 1 && LSF_MAX_INSNS <= 65535, "LsfProgram::n is 16 bits");
static_assert(LSF_MAX_LOCALS <= 255 && LSF_MAX_STACK <= 255, "locals and stack indexes are stored in 8 bits");

// ---------------------------------------------------------------- math ----

static const uint8_t kSin8[256] = {
#include "lsf_sintab.inc"
};

uint8_t lsf_sin8(uint8_t a) { return kSin8[a]; }

uint32_t lsf_hash32(uint32_t x) {  // murmur3 finalizer
  x ^= x >> 16; x *= 0x85ebca6bu;
  x ^= x >> 13; x *= 0xc2b2ae35u;
  x ^= x >> 16;
  return x;
}

static inline int32_t c255(int32_t v) { return v < 0 ? 0 : (v > 255 ? 255 : v); }
static inline uint32_t pack(uint32_t r, uint32_t g, uint32_t b) { return (r << 16) | (g << 8) | b; }

uint32_t lsf_hsv(int32_t h, int32_t s, int32_t v) {
  uint32_t hh = (uint32_t)h & 255u;
  int32_t ss = c255(s), vv = c255(v);
  uint32_t h6 = hh * 6u;
  uint32_t region = h6 >> 8, rem = h6 & 255u;
  uint32_t p = (uint32_t)(vv * (255 - ss)) / 255u;
  uint32_t q = (uint32_t)vv * (255u - ((uint32_t)ss * rem) / 255u) / 255u;
  uint32_t t = (uint32_t)vv * (255u - ((uint32_t)ss * (255u - rem)) / 255u) / 255u;
  uint32_t V = (uint32_t)vv;
  switch (region) {
    case 0: return pack(V, t, p);
    case 1: return pack(q, V, p);
    case 2: return pack(p, V, t);
    case 3: return pack(p, q, V);
    case 4: return pack(t, p, V);
    default: return pack(V, p, q);
  }
}

static uint32_t lattice(int32_t gx, int32_t gy) {
  return lsf_hash32((uint32_t)gx * 374761393u + (uint32_t)gy * 668265263u + 0x9e3779b9u) & 255u;
}
static uint32_t fade255(uint32_t f) { return (f * f * (765u - 2u * f)) / 65025u; }  // smoothstep, 0..255

static int32_t noise2(int32_t a, int32_t b) {  // 8.8 fixed-point coordinates -> 0..255
  int32_t gx = a >> 8, gy = b >> 8;  // arithmetic shift: floor
  uint32_t fx = fade255((uint32_t)(a & 255)), fy = fade255((uint32_t)(b & 255));
  uint32_t n00 = lattice(gx, gy), n10 = lattice(gx + 1, gy);
  uint32_t n01 = lattice(gx, gy + 1), n11 = lattice(gx + 1, gy + 1);
  uint32_t top = (n00 * (255u - fx) + n10 * fx) / 255u;
  uint32_t bot = (n01 * (255u - fx) + n11 * fx) / 255u;
  return (int32_t)((top * (255u - fy) + bot * fy) / 255u);
}

static uint32_t scaleColor(uint32_t c, int32_t f) {
  uint32_t k = (uint32_t)c255(f);
  return pack((((c >> 16) & 255u) * k) / 255u, (((c >> 8) & 255u) * k) / 255u, ((c & 255u) * k) / 255u);
}
static uint32_t blendColor(uint32_t a, uint32_t b, int32_t f) {
  uint32_t k = (uint32_t)c255(f), ik = 255u - k;
  return pack(((((a >> 16) & 255u) * ik) + (((b >> 16) & 255u) * k)) / 255u,
              ((((a >> 8) & 255u) * ik) + (((b >> 8) & 255u) * k)) / 255u,
              (((a & 255u) * ik) + ((b & 255u) * k)) / 255u);
}

// ------------------------------------------------------------ tables ------

struct FnInfo { const char* name; uint8_t arity; uint8_t cost; };
static const FnInfo kFn[FN__COUNT] = {
  {"sin", 1, 2},  {"tri", 1, 2},   {"noise", 2, 8}, {"hash", 1, 4},
  {"scale", 3, 3}, {"min", 2, 1},  {"max", 2, 1},    {"abs", 1, 1},
  {"clamp", 3, 2}, {"mix", 3, 3},  {"sel", 3, 1},    {"hsv", 3, 8},
  {"rgb", 3, 3},   {"pal", 1, 6},  {"red", 1, 1},    {"green", 1, 1},
  {"blue", 1, 1},   {"dim", 2, 6},  {"blend", 3, 10}, {"prev", 1, 3},
  {"prev2", 2, 4},
};

int lsf_fn_arity(uint8_t fn) { return fn < FN__COUNT ? kFn[fn].arity : -1; }

static const char* const kInName[LSF_NIN] = {
  "x", "y", "i", "n", "t", "sp", "ix", "c1", "c2", "c3", "w", "h", "k0", "k1", "k2", "cx", "cy"
};

// Stack effect (pops, pushes) of each opcode, for the verifier and the compiler.
static void opEffect(uint8_t op, int32_t imm, int* pops, int* pushes) {
  switch (op) {
    case OP_CONST: case OP_IN: case OP_LOAD: *pops = 0; *pushes = 1; break;
    case OP_STORE: *pops = 1; *pushes = 0; break;
    case OP_NEG: case OP_LNOT: case OP_BNOT: *pops = 1; *pushes = 1; break;
    case OP_CALL: *pops = lsf_fn_arity((uint8_t)imm); *pushes = 1; break;
    default: *pops = 2; *pushes = 1; break;  // binary operators
  }
}

static uint8_t opCost(uint8_t op, int32_t imm) {
  if (op == OP_CALL) return kFn[imm].cost;
  if (op == OP_MUL || op == OP_DIV || op == OP_MOD) return 2;
  return 1;
}

// -------------------------------------------------------- admission -------

static uint64_t perPixelX10(const LsfProgram& p) {
  return (uint64_t)p.cost * LSF_US_PER_COST_X10 + LSF_US_PER_PIXEL_X10;   // microseconds x 10
}

uint32_t lsf_estimate_us(const LsfProgram& p, uint32_t pixels) {
  uint64_t us = ((uint64_t)pixels * perPixelX10(p) + 9u) / 10u;   // round up: never under-estimate
  return us > 0xFFFFFFFFull ? 0xFFFFFFFFu : (uint32_t)us;
}

uint32_t lsf_max_pixels(const LsfProgram& p, uint32_t budgetUs) {
  uint64_t px = (uint64_t)budgetUs * 10u / perPixelX10(p);   // perPixelX10 >= 10, never zero
  return px > 0xFFFFFFFFull ? 0xFFFFFFFFu : (uint32_t)px;
}

// ---------------------------------------------------------- verifier ------

const char* lsf_verify(const LsfProgram& p) {
  if (p.n == 0 || p.n > LSF_MAX_INSNS) return "bad length";
  if (p.nlocals > LSF_MAX_LOCALS) return "too many locals";
  int depth = 0, maxd = 0;
  bool stored[LSF_MAX_LOCALS] = {false};
  for (unsigned k = 0; k < p.n; k++) {
    const LsfInsn& in = p.code[k];
    if (in.op >= OP__COUNT) return "bad opcode";
    if (in.op == OP_IN && (in.imm < 0 || in.imm >= LSF_NIN)) return "bad input";
    if (in.op == OP_STORE || in.op == OP_LOAD) {
      if (in.imm < 0 || in.imm >= p.nlocals) return "bad local";
      if (in.op == OP_LOAD && !stored[in.imm]) return "local read before write";
    }
    if (in.op == OP_CALL && (in.imm < 0 || in.imm >= FN__COUNT)) return "bad function";
    int pops, pushes;
    opEffect(in.op, in.imm, &pops, &pushes);
    if (depth < pops) return "stack underflow";
    depth += pushes - pops;
    if (depth > maxd) maxd = depth;
    if (depth > LSF_MAX_STACK) return "stack overflow";
    if (in.op == OP_STORE) stored[in.imm] = true;
  }
  if (depth != 1) return "must leave exactly one value";
  return nullptr;
}

// ----------------------------------------------------------- run ----------

uint32_t lsf_run(const LsfProgram& p, const LsfCtx& c) {
  int32_t st[LSF_MAX_STACK + 1];
  int32_t loc[LSF_MAX_LOCALS];
  int sp = 0;
  for (unsigned k = 0; k < p.n; k++) {
    const LsfInsn& in = p.code[k];
    switch (in.op) {
      case OP_CONST: st[sp++] = in.imm; break;
      case OP_IN:    st[sp++] = c.in[in.imm]; break;
      case OP_LOAD:  st[sp++] = loc[in.imm]; break;
      case OP_STORE: loc[in.imm] = st[--sp]; break;
      case OP_NEG:   st[sp - 1] = (int32_t)(0u - (uint32_t)st[sp - 1]); break;
      case OP_LNOT:  st[sp - 1] = st[sp - 1] == 0; break;
      case OP_BNOT:  st[sp - 1] = ~st[sp - 1]; break;
      case OP_CALL: {
        int ar = kFn[in.imm].arity;
        int32_t* a = &st[sp - ar];
        int32_t r = 0;
        switch (in.imm) {
          case FN_SIN:   r = kSin8[(uint32_t)a[0] & 255u]; break;
          case FN_TRI:   { uint32_t v = (uint32_t)a[0] & 255u; r = (int32_t)(v < 128u ? v * 2u : (255u - v) * 2u); } break;
          case FN_NOISE: r = noise2(a[0], a[1]); break;
          case FN_HASH:  r = (int32_t)(lsf_hash32((uint32_t)a[0]) & 0xFFFFu); break;
          case FN_SCALE: r = (int32_t)((int64_t)a[1] + ((int64_t)c255(a[0]) * ((int64_t)a[2] - a[1])) / 255); break;
          case FN_MIN:   r = a[0] < a[1] ? a[0] : a[1]; break;
          case FN_MAX:   r = a[0] > a[1] ? a[0] : a[1]; break;
          case FN_ABS:   r = a[0] < 0 ? (int32_t)(0u - (uint32_t)a[0]) : a[0]; break;
          case FN_CLAMP: { int32_t lo = a[1], hi = a[2]; if (lo > hi) { int32_t t = lo; lo = hi; hi = t; } r = a[0] < lo ? lo : (a[0] > hi ? hi : a[0]); } break;
          case FN_MIX:   r = (int32_t)((int64_t)a[0] + ((int64_t)(a[1] - (int64_t)a[0]) * c255(a[2])) / 255); break;
          case FN_SEL:   r = a[0] ? a[1] : a[2]; break;
          case FN_HSV:   r = (int32_t)lsf_hsv(a[0], a[1], a[2]); break;
          case FN_RGB:   r = (int32_t)pack((uint32_t)c255(a[0]), (uint32_t)c255(a[1]), (uint32_t)c255(a[2])); break;
          case FN_PAL:   r = c.pal ? (int32_t)(c.pal(c.user, (uint8_t)((uint32_t)a[0] & 255u)) & 0xFFFFFFu) : 0; break;
          case FN_R:     r = (a[0] >> 16) & 255; break;
          case FN_G:     r = (a[0] >> 8) & 255; break;
          case FN_B:     r = a[0] & 255; break;
          case FN_DIM:   r = (int32_t)scaleColor((uint32_t)a[0] & 0xFFFFFFu, a[1]); break;
          case FN_BLEND: r = (int32_t)blendColor((uint32_t)a[0] & 0xFFFFFFu, (uint32_t)a[1] & 0xFFFFFFu, a[2]); break;
          case FN_PREV:  r = c.prev1 ? (int32_t)(c.prev1(c.user, (int32_t)((uint32_t)c.in[LSF_IN_I] + (uint32_t)a[0])) & 0xFFFFFFu) : 0; break;
          case FN_PREV2: r = c.prev2 ? (int32_t)(c.prev2(c.user, (int32_t)((uint32_t)c.in[LSF_IN_CX] + (uint32_t)a[0]),
                                                          (int32_t)((uint32_t)c.in[LSF_IN_CY] + (uint32_t)a[1])) & 0xFFFFFFu) : 0; break;
        }
        sp -= ar;
        st[sp++] = r;
      } break;
      default: {  // binary operators
        int32_t b = st[--sp], a = st[sp - 1], r = 0;
        uint32_t ua = (uint32_t)a, ub = (uint32_t)b;
        switch (in.op) {
          case OP_ADD: r = (int32_t)(ua + ub); break;
          case OP_SUB: r = (int32_t)(ua - ub); break;
          case OP_MUL: r = (int32_t)(ua * ub); break;
          case OP_DIV: r = (b == 0) ? 0 : (b == -1 ? (int32_t)(0u - ua) : a / b); break;
          case OP_MOD: r = (b == 0 || b == -1) ? 0 : a % b; break;
          case OP_SHL: r = (int32_t)(ua << (ub & 31u)); break;
          case OP_SHR: r = a >> (ub & 31u); break;
          case OP_AND: r = a & b; break;
          case OP_OR:  r = a | b; break;
          case OP_XOR: r = a ^ b; break;
          case OP_LT:  r = a < b; break;
          case OP_GT:  r = a > b; break;
          case OP_LE:  r = a <= b; break;
          case OP_GE:  r = a >= b; break;
          case OP_EQ:  r = a == b; break;
          case OP_NE:  r = a != b; break;
          case OP_LAND: r = (a != 0) && (b != 0); break;
          case OP_LOR:  r = (a != 0) || (b != 0); break;
        }
        st[sp - 1] = r;
      } break;
    }
  }
  return (uint32_t)st[0] & 0xFFFFFFu;
}

// -------------------------------------------------------- compiler --------

namespace {

struct Compiler {
  const char* s;
  size_t n, p;
  LsfProgram* P;
  LsfError* E;
  int depth;     // current stack depth while emitting
  int maxDepth;
  int nest;      // parser recursion depth
  int parens;    // open '(' count: newlines are plain whitespace inside parentheses
  unsigned cost;
  char names[LSF_MAX_LOCALS][LSF_MAX_NAME + 1];
  bool failed;

  void fail(const char* msg, size_t at) {
    if (failed) return;
    failed = true;
    size_t i = 0;
    for (; msg[i] && i < sizeof(E->msg) - 1; i++) E->msg[i] = msg[i];
    E->msg[i] = 0;
    E->pos = (uint16_t)at;
    unsigned line = 1;
    for (size_t k = 0; k < at && k < n; k++) if (s[k] == '\n') line++;
    E->line = (uint16_t)line;
  }

  void emit(uint8_t op, int32_t imm) {
    if (failed) return;
    if (P->n >= LSF_MAX_INSNS) { fail("program too long", p); return; }
    int pops, pushes;
    opEffect(op, imm, &pops, &pushes);
    depth += pushes - pops;
    if (depth > maxDepth) maxDepth = depth;
    if (depth > LSF_MAX_STACK) { fail("expression too deep", p); return; }
    LsfInsn& in = P->code[P->n++];
    in.op = op; in.pad[0] = in.pad[1] = in.pad[2] = 0; in.imm = imm;
    cost += opCost(op, imm);
  }

  static bool isIdStart(char c) { return (c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || c == '_'; }
  static bool isDigit(char c) { return c >= '0' && c <= '9'; }

  void skipSpace(bool newlines) {
    for (;;) {
      if (p >= n) return;
      char c = s[p];
      if (c == ' ' || c == '\t' || c == '\r') p++;
      else if (c == '\n' && (newlines || parens > 0)) p++;
      else if (c == '#') { while (p < n && s[p] != '\n') p++; }
      else return;
    }
  }

  bool matchWord(const char* w) {  // identifier-boundary keyword match
    size_t l = strlen(w);
    if (p + l > n || memcmp(s + p, w, l) != 0) return false;
    if (p + l < n && (isIdStart(s[p + l]) || isDigit(s[p + l]))) return false;
    p += l;
    return true;
  }

  int findLocal(const char* id, size_t l) {
    for (unsigned k = 0; k < P->nlocals; k++)
      if (strlen(names[k]) == l && memcmp(names[k], id, l) == 0) return (int)k;
    return -1;
  }
  static int findInput(const char* id, size_t l) {
    for (int k = 0; k < LSF_NIN; k++)
      if (strlen(kInName[k]) == l && memcmp(kInName[k], id, l) == 0) return k;
    return -1;
  }
  static int findFn(const char* id, size_t l) {
    for (int k = 0; k < FN__COUNT; k++)
      if (strlen(kFn[k].name) == l && memcmp(kFn[k].name, id, l) == 0) return k;
    return -1;
  }

  // Binary operator table for the precedence climber.
  struct BinOp { const char* tok; uint8_t prec; uint8_t op; };
  const BinOp* peekBinary() {
    static const BinOp ops[] = {
      {"||", 1, OP_LOR}, {"&&", 2, OP_LAND}, {"|", 3, OP_OR}, {"^", 4, OP_XOR}, {"&", 5, OP_AND},
      {"==", 6, OP_EQ}, {"!=", 6, OP_NE}, {"<=", 7, OP_LE}, {">=", 7, OP_GE}, {"<<", 8, OP_SHL},
      {">>", 8, OP_SHR}, {"<", 7, OP_LT}, {">", 7, OP_GT}, {"+", 9, OP_ADD}, {"-", 9, OP_SUB},
      {"*", 10, OP_MUL}, {"/", 10, OP_DIV}, {"%", 10, OP_MOD},
    };
    skipSpace(false);
    for (unsigned k = 0; k < sizeof(ops) / sizeof(ops[0]); k++) {
      size_t l = strlen(ops[k].tok);
      if (p + l <= n && memcmp(s + p, ops[k].tok, l) == 0) {
        // `|` must not eat the first half of `||`, etc.: longer tokens are listed first where it matters.
        return &ops[k];
      }
    }
    return nullptr;
  }

  void parseExpr(int minPrec) {
    if (failed) return;
    if (++nest > 24) { fail("expression too deep", p); nest--; return; }
    parseUnary();
    for (;;) {
      if (failed) break;
      const BinOp* b = peekBinary();
      if (!b || b->prec < minPrec) break;
      p += strlen(b->tok);
      parseExpr(b->prec + 1);
      emit(b->op, 0);
    }
    nest--;
  }

  void parseUnary() {
    skipSpace(false);
    if (p >= n) { fail("expression expected", p); return; }
    char c = s[p];
    if (c == '-' || c == '!' || c == '~') {
      p++;
      if (++nest > 24) { fail("expression too deep", p); nest--; return; }
      parseUnary();
      nest--;
      emit(c == '-' ? OP_NEG : (c == '!' ? OP_LNOT : OP_BNOT), 0);
      return;
    }
    parsePrimary();
  }

  void parsePrimary() {
    skipSpace(false);
    if (p >= n) { fail("expression expected", p); return; }
    char c = s[p];
    if (c == '(') {
      p++;
      parens++;
      parseExpr(1);
      skipSpace(false);
      if (p >= n || s[p] != ')') { fail("')' expected", p); return; }
      p++;
      parens--;
      return;
    }
    if (isDigit(c)) { parseNumber(); return; }
    if (isIdStart(c)) { parseName(); return; }
    if (c == '\n' || c == ';') { fail("expression expected", p); return; }
    fail("unexpected character", p);
  }

  void parseNumber() {
    size_t start = p;
    uint64_t v = 0;
    if (s[p] == '0' && p + 1 < n && (s[p + 1] == 'x' || s[p + 1] == 'X')) {
      p += 2;
      size_t digits = 0;
      while (p < n) {
        char c = s[p]; int d;
        if (c >= '0' && c <= '9') d = c - '0';
        else if (c >= 'a' && c <= 'f') d = c - 'a' + 10;
        else if (c >= 'A' && c <= 'F') d = c - 'A' + 10;
        else break;
        v = v * 16 + (uint64_t)d; digits++; p++;
        if (v > 0xFFFFFFFFull) { fail("number too large", start); return; }
      }
      if (digits == 0) { fail("hex digits expected", start); return; }
    } else {
      while (p < n && isDigit(s[p])) {
        v = v * 10 + (uint64_t)(s[p] - '0'); p++;
        if (v > 0x7FFFFFFFull) { fail("number too large", start); return; }
      }
    }
    if (p < n && (s[p] == '.' || isIdStart(s[p]))) { fail("integers only", p); return; }
    emit(OP_CONST, (int32_t)(uint32_t)v);
  }

  void parseName() {
    size_t start = p;
    while (p < n && (isIdStart(s[p]) || isDigit(s[p]))) p++;
    size_t l = p - start;
    const char* id = s + start;
    skipSpace(false);
    if (p < n && s[p] == '(') {  // function call
      int f = findFn(id, l);
      if (f < 0) { fail("unknown function", start); return; }
      p++;
      parens++;
      int argc = 0;
      skipSpace(false);
      if (p < n && s[p] == ')') {
        p++;
      } else {
        for (;;) {
          parseExpr(1);
          if (failed) return;
          argc++;
          skipSpace(false);
          if (p < n && s[p] == ',') { p++; continue; }
          if (p < n && s[p] == ')') { p++; break; }
          fail("',' or ')' expected", p);
          return;
        }
      }
      parens--;
      if (argc != kFn[f].arity) { fail("wrong argument count", start); return; }
      if (f == FN_PREV || f == FN_PREV2) P->usesPrev = true;
      emit(OP_CALL, f);
      return;
    }
    int loc = findLocal(id, l);
    if (loc >= 0) { emit(OP_LOAD, loc); return; }
    int in = findInput(id, l);
    if (in >= 0) { emit(OP_IN, in); return; }
    fail("unknown name", start);
  }

  void parseProgram() {
    bool haveFinal = false;
    for (;;) {
      // statement separators: newline or ';'
      for (;;) {
        skipSpace(true);
        if (p < n && s[p] == ';') p++; else break;
      }
      if (p >= n) break;
      if (haveFinal) { fail("final expression must be last", p); return; }
      if (matchWord("let")) {
        skipSpace(false);
        size_t ns = p;
        if (p >= n || !isIdStart(s[p])) { fail("name expected", p); return; }
        while (p < n && (isIdStart(s[p]) || isDigit(s[p]))) p++;
        size_t l = p - ns;
        if (l > LSF_MAX_NAME) { fail("name too long", ns); return; }
        if (findInput(s + ns, l) >= 0 || findFn(s + ns, l) >= 0 || findLocal(s + ns, l) >= 0 ||
            (l == 3 && memcmp(s + ns, "let", 3) == 0)) { fail("name already in use", ns); return; }
        if (P->nlocals >= LSF_MAX_LOCALS) { fail("too many variables", ns); return; }
        skipSpace(false);
        if (p >= n || s[p] != '=') { fail("'=' expected", p); return; }
        p++;
        parseExpr(1);
        if (failed) return;
        // The name becomes visible only after its own definition (no self reference).
        unsigned idx = P->nlocals++;
        memcpy(names[idx], s + ns, l); names[idx][l] = 0;
        emit(OP_STORE, (int32_t)idx);
      } else {
        parseExpr(1);
        if (failed) return;
        haveFinal = true;
        skipSpace(false);
        if (p < n && s[p] != '\n' && s[p] != ';') { fail("unexpected text after expression", p); return; }
      }
    }
    if (!haveFinal) fail("missing final expression", n);
  }
};

}  // namespace

bool lsf_compile(const char* src, size_t len, LsfProgram* out, LsfError* err) {
  LsfError dummy;
  if (!err) err = &dummy;
  err->msg[0] = 0; err->pos = 0; err->line = 1;
  if (!src || !out) return false;
  memset(out, 0, sizeof(*out));
  Compiler c;
  c.s = src; c.n = len; c.p = 0; c.P = out; c.E = err;
  c.depth = 0; c.maxDepth = 0; c.nest = 0; c.parens = 0; c.cost = 0; c.failed = false;
  memset(c.names, 0, sizeof(c.names));
  if (len > LSF_MAX_SRC) { c.fail("source too long", 0); return false; }
  for (size_t k = 0; k < len; k++)
    if (src[k] == 0) { c.fail("NUL in source", k); return false; }
  c.parseProgram();
  if (c.failed) return false;
  out->maxstack = (uint8_t)c.maxDepth;
  out->cost = (uint16_t)(c.cost > 65535 ? 65535 : c.cost);
  const char* why = lsf_verify(*out);
  if (why) {  // compiler bug guard
    c.fail(why, 0);
    return false;
  }
  return true;
}
