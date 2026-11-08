// Desktop renderer for LS Formula programs: compile a file and print pixels.
//   lsf_cli FILE.lsf [-n LEDS] [-t MS] [-f FRAMES] [-w W -h H] [-sp N -ix N]
// 1D output is one hex row per frame. With -w/-h the frame is printed as a grid
// and a previous frame is kept, so prev()/prev2() work as on the device.
#include "../lsf.h"
#include <stdio.h>
#include <stdlib.h>
#include <string.h>
#include <vector>

struct Host { int w, h; std::vector<uint32_t> prev; };
static uint32_t palCb(void*, uint8_t i) { return lsf_hsv(i, 255, 255); }  // rainbow stand-in
static uint32_t prev1Cb(void* u, int32_t i) { Host* H = (Host*)u; return (i < 0 || i >= (int)H->prev.size()) ? 0 : H->prev[i]; }
static uint32_t prev2Cb(void* u, int32_t x, int32_t y) { Host* H = (Host*)u; return (x < 0 || y < 0 || x >= H->w || y >= H->h) ? 0 : H->prev[y * H->w + x]; }

int main(int argc, char** argv) {
  if (argc < 2) { fprintf(stderr, "usage: %s FILE.lsf [-n LEDS] [-t MS] [-f FRAMES] [-w W -h H] [-sp N] [-ix N]\n", argv[0]); return 2; }
  int n = 16, w = 0, h = 0, frames = 1, sp = 128, ix = 128; unsigned t0 = 0;
  for (int a = 2; a + 1 < argc; a += 2) {
    int v = atoi(argv[a + 1]);
    if (!strcmp(argv[a], "-n")) n = v; else if (!strcmp(argv[a], "-t")) t0 = (unsigned)v;
    else if (!strcmp(argv[a], "-f")) frames = v; else if (!strcmp(argv[a], "-w")) w = v;
    else if (!strcmp(argv[a], "-h")) h = v; else if (!strcmp(argv[a], "-sp")) sp = v; else if (!strcmp(argv[a], "-ix")) ix = v;
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
  Host H; H.w = w; H.h = h; H.prev.assign((size_t)w * h, 0);
  std::vector<uint32_t> next(H.prev.size());
  LsfCtx c; memset(&c, 0, sizeof(c));
  c.user = &H; c.pal = palCb; c.prev1 = prev1Cb; c.prev2 = prev2Cb;
  c.in[LSF_IN_N] = w * h; c.in[LSF_IN_W] = w; c.in[LSF_IN_H] = h; c.in[LSF_IN_SP] = sp; c.in[LSF_IN_IX] = ix;
  c.in[LSF_IN_C1] = 128; c.in[LSF_IN_C2] = 128; c.in[LSF_IN_C3] = 16;
  c.in[LSF_IN_K0] = 0xFF0000; c.in[LSF_IN_K1] = 0x00FF00; c.in[LSF_IN_K2] = 0x0000FF;
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
