// Desktop renderer for LS Formula programs: compile a file and print pixels.
//   lsf_cli FILE.lsf [-n LEDS] [-t MS] [-f FRAMES] [-w W -h H] [-sp N -ix N] [-pal PALETTE.json] [-k1 RRGGBB]
// 1D output is one hex row per frame. With -w/-h the frame is printed as a grid
// and a previous frame is kept, so prev()/prev2() work as on the device.
#include "../lsf.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vector>

struct Host { int w, h; std::vector<uint32_t> prev; bool hasPal; uint8_t pal[16][3]; };
// With -pal, pal() reads a WLED custom palette file ({"palette":[index,r,g,b,...]}) the way WLED does: the gradient is
// sampled into 16 slots (one per 16 index values), and pal() does a no-wrap linear blend after scaling the index by 240/256.
// Without it pal() is a rainbow stand-in.
static uint32_t palCb(void* u, uint8_t i) {
  Host* H = (Host*)u;
  if (!H->hasPal) return lsf_hsv(i, 255, 255);
  unsigned x = (i * 241u) >> 8, hi = x >> 4, lo = x & 15, nx = hi < 15 ? hi + 1 : 15;
  unsigned ch[3];
  for (int k = 0; k < 3; k++) ch[k] = (H->pal[hi][k] * (16 - lo) + H->pal[nx][k] * lo) / 16;
  return (ch[0] << 16) | (ch[1] << 8) | ch[2];
}
static bool loadPalette(const char* path, Host* H) {
  FILE* f = fopen(path, "rb");
  if (!f) { perror(path); return false; }
  char buf[2048]; size_t len = fread(buf, 1, sizeof(buf) - 1, f); fclose(f); buf[len] = 0;
  const char* q = strchr(buf, '[');
  std::vector<int> v;
  while (q && *q && *q != ']') { if (*q >= '0' && *q <= '9') { v.push_back(atoi(q)); while (*q >= '0' && *q <= '9') q++; } else q++; }
  if (v.size() < 8 || v.size() % 4 || v.size() > 72 || v.back() > 255) { fprintf(stderr, "%s: not a WLED palette\n", path); return false; }
  for (size_t k = 0; k + 4 < v.size(); k += 4) {   // fill_gradient_RGB between consecutive stops, in slot units
    int s0 = v[k] / 16, s1 = v[k + 4] / 16;
    for (int sl = s0; sl <= s1 && sl < 16; sl++)
      for (int c = 0; c < 3; c++) H->pal[sl][c] = s1 == s0 ? v[k + 5 + c] : (v[k + 1 + c] * (s1 - sl) + v[k + 5 + c] * (sl - s0)) / (s1 - s0);
  }
  H->hasPal = true;
  return true;
}
static uint32_t prev1Cb(void* u, int32_t i) { Host* H = (Host*)u; return (i < 0 || i >= (int)H->prev.size()) ? 0 : H->prev[i]; }
static uint32_t prev2Cb(void* u, int32_t x, int32_t y) { Host* H = (Host*)u; return (x < 0 || y < 0 || x >= H->w || y >= H->h) ? 0 : H->prev[y * H->w + x]; }

int main(int argc, char** argv) {
  if (argc < 2) { fprintf(stderr, "usage: %s FILE.lsf [-n LEDS] [-t MS] [-f FRAMES] [-w W -h H] [-sp N] [-ix N] [-pal FILE] [-k1 RRGGBB] [-c1 N] [-c2 N] [-c3 N]\n", argv[0]); return 2; }
  int n = 16, w = 0, h = 0, frames = 1, sp = 128, ix = 128, c1 = 128, c2 = 128, c3 = 16; unsigned t0 = 0;
  const char* palFile = nullptr; unsigned k1 = 0x00FF00;
  for (int a = 2; a + 1 < argc; a += 2) {
    int v = atoi(argv[a + 1]);
    if (!strcmp(argv[a], "-pal")) { palFile = argv[a + 1]; continue; }
    if (!strcmp(argv[a], "-k1")) { k1 = (unsigned)strtoul(argv[a + 1], nullptr, 16); continue; }
    if (!strcmp(argv[a], "-n")) n = v; else if (!strcmp(argv[a], "-t")) t0 = (unsigned)v;
    else if (!strcmp(argv[a], "-f")) frames = v; else if (!strcmp(argv[a], "-w")) w = v;
    else if (!strcmp(argv[a], "-h")) h = v; else if (!strcmp(argv[a], "-sp")) sp = v; else if (!strcmp(argv[a], "-ix")) ix = v;
    else if (!strcmp(argv[a], "-c1")) c1 = v; else if (!strcmp(argv[a], "-c2")) c2 = v; else if (!strcmp(argv[a], "-c3")) c3 = v;
  }
  FILE* f = fopen(argv[1], "rb");
  if (!f) { perror(argv[1]); return 2; }
  static char src[LSF_MAX_SRC + 2];
  size_t len = fread(src, 1, sizeof(src), f); fclose(f);
  LsfProgram p; LsfError e;
  if (!lsf_compile(src, len, &p, &e)) { fprintf(stderr, "%s:%u: error: %s\n", argv[1], e.line, e.msg); return 1; }
  fprintf(stderr, "ok: %u instructions, %u locals, stack %u, cost %u, prev=%s\n", p.n, p.nlocals, p.maxstack, p.cost, p.usesPrev ? "yes" : "no");
  bool is2d = w > 0 && h > 0;
  if (!is2d) { w = n; h = 1; }
  Host H; H.w = w; H.h = h; H.prev.assign((size_t)w * h, 0); H.hasPal = false; memset(H.pal, 0, sizeof(H.pal));
  if (palFile && !loadPalette(palFile, &H)) return 2;
  std::vector<uint32_t> next(H.prev.size());
  LsfCtx c; memset(&c, 0, sizeof(c));
  c.user = &H; c.pal = palCb; c.prev1 = prev1Cb; c.prev2 = prev2Cb;
  c.in[LSF_IN_N] = w * h; c.in[LSF_IN_W] = w; c.in[LSF_IN_H] = h; c.in[LSF_IN_SP] = sp; c.in[LSF_IN_IX] = ix;
  c.in[LSF_IN_C1] = c1; c.in[LSF_IN_C2] = c2; c.in[LSF_IN_C3] = c3;
  c.in[LSF_IN_K0] = 0xFF0000; c.in[LSF_IN_K1] = (int32_t)k1; c.in[LSF_IN_K2] = 0x0000FF;
  for (int fr = 0; fr < frames; fr++) {
    c.in[LSF_IN_T] = (int32_t)(t0 + (unsigned)fr * 16u);
    for (int y = 0; y < h; y++) for (int x = 0; x < w; x++) {
      int i = y * w + x;
      c.in[LSF_IN_I] = i; c.in[LSF_IN_CX] = x; c.in[LSF_IN_CY] = y;
      c.in[LSF_IN_X] = w > 1 ? x * 255 / (w - 1) : 0;
      c.in[LSF_IN_Y] = h > 1 ? y * 255 / (h - 1) : 0;
      next[i] = lsf_run(p, c);
    }
    H.prev = next;
    if (fr == frames - 1) {
      for (int y = 0; y < h; y++) {
        for (int x = 0; x < w; x++) printf("%06X ", H.prev[y * w + x]);
        printf("\n");
      }
    }
  }
  return 0;
}
