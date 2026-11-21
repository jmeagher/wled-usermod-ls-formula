// LS Formula usermod for WLED: effects defined at run time by uploaded formulas.
//
// Registers LSF_SLOTS effects ("LS Formula 1".."N"). Each slot holds one
// formula in the small integer language of lsf.h. Formulas are uploaded through
// the normal WLED JSON state API (POST /json/state), validated before use, and
// persisted in cfg.json. See README.md.
#include "wled.h"

#ifdef ARDUINO_ARCH_ESP32

#include <new>
#include "lsf.h"
#include "lsf_button.h"
#include "lsf_runtime.h"

#ifndef LSF_SLOTS
#define LSF_SLOTS 4
#endif
#ifndef LSF_SLOW_FRAME_US
#define LSF_SLOW_FRAME_US 60000   // a segment frame this slow, three times in a row, disables the formula; also the
                                  // real time after which no more formula segments are drawn in the same strip frame
#endif
#ifndef LSF_BUDGET_US
#define LSF_BUDGET_US 15000       // largest estimated render time per frame for one formula (see lsf_estimate_us)
#endif
#ifndef LSF_FRAME_BUDGET_US
#define LSF_FRAME_BUDGET_US (2 * LSF_BUDGET_US)   // largest estimated time for all formula segments of one strip frame
#endif
#ifndef LSF_SWAP_WAIT_MS
#define LSF_SWAP_WAIT_MS 500      // how long an install waits for the frame being drawn to release a program buffer
#endif
#define LSF_STATUS_STALE_MS 2000  // per-frame status older than this is not reported (the effect is not running)
#define LSF_NAME_MAX 20

// Concurrency. WLED draws every segment from one task (strip.service() in loop()); uploads, settings and the state
// JSON run in the web server's task. The program a frame is drawing with is never written: an install compiles into
// a spare buffer and publishes it atomically (LsfProgramStore, lsf_runtime.h), so the strip is not suspended.
// Everything else here (source, name, g_last, the store's writer side) is only touched with WLED's JSON buffer lock
// held: readFromJsonState, addToJsonState, readFromConfig and addToConfig are all called under it.
struct LsfSlot {
  uint8_t    fxId = 255;         // WLED effect id once registered
  uint16_t   srcLen = 0;
  char       name[LSF_NAME_MAX + 1];
  char       src[LSF_MAX_SRC + 1];
  char       meta[96];           // WLED effect metadata string, rebuilt when the name changes
  char       cfgErr[48];         // a stored formula that did not compile at boot (the slot is then empty)
};

static LsfSlot g_slots[LSF_SLOTS];
static LsfProgramStore<LSF_SLOTS> g_store;
static LsfFrameGuard<LSF_SLOTS> g_frame(LSF_BUDGET_US, LSF_FRAME_BUDGET_US, LSF_SLOW_FRAME_US);
static uint16_t g_installGen = 0;

// Result of the most recent upload, dry run or settings change, reported in the state JSON.
struct LsfLast {
  int      slot = -1;
  bool     ok = false;
  bool     dry = false;
  char     msg[72] = "";
  uint16_t line = 0, pos = 0, instrs = 0, cost = 0;
  uint32_t estUs = 0, maxPixels = 0;
};
static LsfLast g_last;

static void buildMeta(LsfSlot& s) {
  // Format: Name@slider labels;colour labels;palette;dimension flags;defaults
  // "12" = usable on 1D and 2D segments. See https://kno.wled.ge/interfaces/json-api/#effect-metadata
  snprintf(s.meta, sizeof(s.meta), "%s@Speed,Intensity,P1,P2,P3;!,!,!;!;12;sx=128,ix=128", s.name);
}

static void setName(LsfSlot& s, uint8_t idx, const char* wanted) {
  char nm[LSF_NAME_MAX + 1];
  size_t o = 0;
  if (wanted) {
    for (; *wanted && o < LSF_NAME_MAX; wanted++) {   // the metadata format reserves '@' ';' ','
      char c = *wanted;
      if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == ' ' || c == '-' || c == '_') nm[o++] = c;
    }
  }
  nm[o] = 0;
  if (o == 0) snprintf(nm, sizeof(nm), "LS Formula %u", (unsigned)idx + 1);
  if (strcmp(nm, s.name) == 0 && s.meta[0]) return;   // unchanged: do not rewrite the metadata WLED may be reading
  memcpy(s.name, nm, sizeof(nm));
  buildMeta(s);
}

// ------------------------------------------------------------ rendering ---

struct RenderState { uint8_t* prev; int w, h; unsigned total; };

static uint32_t cbPalette(void*, uint8_t idx) {
  return SEGMENT.color_from_palette(idx, false, false, 0) & 0xFFFFFFu;
}
static uint32_t cbPrev1(void* u, int32_t i) {
  const RenderState* r = (const RenderState*)u;
  if (i < 0 || (unsigned)i >= r->total) return 0;
  const uint8_t* p = r->prev + (size_t)i * 3;
  return ((uint32_t)p[0] << 16) | ((uint32_t)p[1] << 8) | p[2];
}
static uint32_t cbPrev2(void* u, int32_t x, int32_t y) {
  const RenderState* r = (const RenderState*)u;
  if (x < 0 || y < 0 || x >= r->w || y >= r->h) return 0;
  return cbPrev1(u, y * r->w + x);
}

static void renderSlot(uint8_t s) {
  LsfProgramStore<LSF_SLOTS>::Reader reader(g_store);   // the program stays valid until this returns
  LsfRun* run = reader.get(s);
  if (!run || run->disabled.load()) { SEGMENT.fill(BLACK); return; }
  const LsfProgram& prog = run->prog;

  const bool two = strip.isMatrix && SEGMENT.is2D();
  const int w = two ? (int)SEG_W : (int)SEGLEN;
  const int h = two ? (int)SEG_H : 1;
  const unsigned total = (unsigned)w * (unsigned)h;
  if (total == 0) return;

  // Admission control, every frame: an LED-count or segment change can make an accepted formula too slow for this
  // segment, and all formula segments of one frame share LSF_FRAME_BUDGET_US.
  const uint32_t estUs = lsf_estimate_us(prog, total);
  if (g_frame.admit(s, (uint32_t)strip.now, millis(), estUs, total) != LsfFrameGuard<LSF_SLOTS>::DRAW) {
    SEGMENT.fill(BLACK);
    return;
  }

  RenderState rs = { nullptr, w, h, total };
  uint8_t* next = nullptr;
  if (prog.usesPrev) {
    // Two RGB frames live in the segment's effect data: previous, then the one being built.
    if (!SEGENV.allocateData((size_t)total * 6)) {   // heap pressure: show the first colour instead of crashing
      SEGMENT.fill(SEGCOLOR(0));
      return;
    }
    rs.prev = SEGENV.data;
    next = SEGENV.data + (size_t)total * 3;
  }

  LsfCtx c;
  memset(&c, 0, sizeof(c));
  c.user = &rs;
  c.pal = cbPalette;
  if (rs.prev) { c.prev1 = cbPrev1; c.prev2 = cbPrev2; }
  c.in[LSF_IN_N] = (int32_t)total;
  c.in[LSF_IN_T] = (int32_t)(uint32_t)strip.now;
  c.in[LSF_IN_SP] = SEGMENT.speed;
  c.in[LSF_IN_IX] = SEGMENT.intensity;
  c.in[LSF_IN_C1] = SEGMENT.custom1;
  c.in[LSF_IN_C2] = SEGMENT.custom2;
  c.in[LSF_IN_C3] = SEGMENT.custom3;
  c.in[LSF_IN_W] = w;
  c.in[LSF_IN_H] = h;
  c.in[LSF_IN_K0] = (int32_t)(SEGCOLOR(0) & 0xFFFFFFu);
  c.in[LSF_IN_K1] = (int32_t)(SEGCOLOR(1) & 0xFFFFFFu);
  c.in[LSF_IN_K2] = (int32_t)(SEGCOLOR(2) & 0xFFFFFFu);

  const uint32_t t0 = micros();
  for (int y = 0; y < h; y++) {
    c.in[LSF_IN_Y] = h > 1 ? (y * 255) / (h - 1) : 0;
    c.in[LSF_IN_CY] = y;
    for (int x = 0; x < w; x++) {
      const int i = y * w + x;
      c.in[LSF_IN_I] = i;
      c.in[LSF_IN_CX] = x;
      c.in[LSF_IN_X] = w > 1 ? (x * 255) / (w - 1) : 0;
      const uint32_t col = lsf_run(prog, c);
      if (next) {
        uint8_t* q = next + (size_t)i * 3;
        q[0] = col >> 16; q[1] = col >> 8; q[2] = col;
      }
      if (two) SEGMENT.setPixelColorXY(x, y, col); else SEGMENT.setPixelColor(i, col);
    }
  }
  if (next) memcpy(rs.prev, next, (size_t)total * 3);

  // Slow-frame guard: a formula that cannot keep up must not stall the strip service loop (and WiFi with it).
  // Counted per segment (aux0, with the install generation in aux1): a fast small segment must not reset the count
  // of a slow big one running the same formula.
  const uint32_t us = micros() - t0;
  g_frame.charge(us);
  if (us > run->worstUs.load(std::memory_order_relaxed)) run->worstUs.store(us, std::memory_order_relaxed);
  const uint32_t avg = run->avgUs.load(std::memory_order_relaxed);
  run->avgUs.store(avg ? (avg * 15 + us) / 16 : us, std::memory_order_relaxed);
  if (lsfSlowFrame(SEGENV.aux0, SEGENV.aux1, run->gen, us, LSF_SLOW_FRAME_US) && !run->disabled.load()) {
    snprintf(run->err, sizeof(run->err), "disabled: frame took %lu ms", (unsigned long)(us / 1000));
    run->disabled.store(true);   // after err: a reader that sees `disabled` sees the message
  }
}

template <uint8_t S> static void mode_lsf(void) { renderSlot(S); }

// -------------------------------------------------------------- usermod ---

class LsFormulaUsermod : public Usermod {
 private:
  static const char _name[];
  bool registered = false;

  // --- one-button control (see lsf_button.h). Off unless a pin is configured (Settings > Usermods). -------------
  int8_t   btnPin = -1;            // GPIO of the button; -1 = feature off
  bool     btnActiveLow = true;    // pressed pulls the pin to ground (the BOOT button on most ESP32 boards)
  uint8_t  briStepPct = 5;         // brightness step while held, in percent (1..50)
  int8_t   btnOwned = -1;          // the pin this usermod has actually claimed from the PinManager
  LsfButton btn;
  LsfIdSet presets;                // ids of the presets that exist, rebuilt when presets.json changes
  unsigned long presetsStamp = 0;  // presetsModifiedTime the list was built for
  bool     presetsValid = false;

  void releaseButton() {
    if (btnOwned >= 0) PinManager::deallocatePin(btnOwned, PinOwner::UM_Unspecified);
    btnOwned = -1;
    btn.reset();
  }

  void initButton() {
    releaseButton();
    if (btnPin < 0) return;
    if (!PinManager::allocatePin(btnPin, false, PinOwner::UM_Unspecified)) {
      DEBUG_PRINTF_P(PSTR("[lsf] button pin %d is not available (used by something else or not a valid input)\n"), btnPin);
      return;
    }
    btnOwned = btnPin;
    pinMode(btnPin, btnActiveLow ? INPUT_PULLUP : INPUT_PULLDOWN);
  }

  // The preset ids come from one pass over presets.json that keeps only each preset's "n" key. Checking the ids one
  // by one would open the file up to 250 times.
  void loadPresetList() {
    if (presetsValid && presetsStamp == presetsModifiedTime) return;
    if (!requestJSONBufferLock(JSON_LOCK_PRESET_NAME)) return;   // busy: keep the old list, try again on the next press
    StaticJsonDocument<64> filter;
    filter["*"]["n"] = true;
    presets.clear();
    if (readObjectFromFile(getPresetsFileName(), nullptr, pDoc, &filter)) {
      for (JsonPair kv : pDoc->as<JsonObject>()) {
        const int id = atoi(kv.key().c_str());
        if (id >= 1 && id <= LsfIdSet::kMax && !kv.value()["n"].isNull()) presets.add(id);
      }
    }
    releaseJSONBufferLock();
    presetsStamp = presetsModifiedTime;
    presetsValid = true;
  }

  void nextPreset() {
    loadPresetList();
    const unsigned id = presets.next(currentPreset);
    if (id == 0) return;                                   // no presets saved yet
    if (bri == 0) toggleOnOff();                           // a click on a dark display should show something
    applyPreset(id, CALL_MODE_BUTTON_PRESET);
  }

  void stepBrightness(bool up, uint32_t now) {
    if (up) {
      if (bri == 0) {
        toggleOnOff();                                     // power on, then start from the lowest step
        bri = lsfNextBrightness(0, briStepPct);
      } else {
        bri = lsfNextBrightness(bri, briStepPct);
      }
    } else {
      if (bri == 0) return;                                // nothing to dim; dimming never turns the display off
      const uint8_t v = lsfPrevBrightness(bri, briStepPct);
      if (v == bri) return;                                // already at the lowest level
      bri = v;
    }
    stateUpdated(CALL_MODE_BUTTON);
    if (lsfAtBrightnessEnd(bri)) btn.delayNextStep(now, LSF_BTN_END_HOLD_MS);   // dwell on the brightest / dimmest level
  }

  void pollButton() {
    if (btnOwned < 0) return;
    const bool pressed = (digitalRead(btnOwned) == LOW) == btnActiveLow;
    const uint32_t now = millis();
    switch (btn.poll(pressed, now)) {
      case LSFB_CLICK:     nextPreset(); break;
      case LSFB_DOUBLE:    toggleOnOff(); stateUpdated(CALL_MODE_BUTTON); break;
      case LSFB_HOLD_UP:   stepBrightness(true, now); break;
      case LSFB_HOLD_DOWN: stepBrightness(false, now); break;
      default: break;
    }
  }

  // A program buffer that the frame being drawn cannot be reading. Normally immediate; it waits only when an install
  // follows the previous one while a frame still uses the program that install replaced.
  LsfRun* acquireBuffer() {
    LsfRun* b = g_store.acquire();
    if (b || !registered) return b;   // before setup() nothing is drawing
    const uint32_t start = millis();
    while (!(b = g_store.acquire()) && millis() - start < LSF_SWAP_WAIT_MS) delay(1);
    return b;
  }

  // Compile `src` and, unless `dry`, install it in slot `idx`. Returns true on success and fills `out`. On failure
  // nothing about the slot changes: the previous formula keeps running.
  bool install(uint8_t idx, const char* name, const char* src, bool dry, LsfLast& out) {
    out = LsfLast();
    out.slot = idx; out.dry = dry;
    if (idx >= LSF_SLOTS) { snprintf(out.msg, sizeof(out.msg), "slot out of range"); return false; }
    const size_t len = strlen(src);
    LsfRun* b = acquireBuffer();
    if (!b) { snprintf(out.msg, sizeof(out.msg), "busy: a frame is still being drawn, try again"); return false; }
    // `b` belongs to no slot and no frame is reading it, so it can be compiled into directly (it is 2 KB: too big
    // for the web server's stack). A failed or dry compile just leaves it spare.
    LsfError err;
    if (!lsf_compile(src, len, &b->prog, &err)) {
      snprintf(out.msg, sizeof(out.msg), "%s", err.msg);
      out.line = err.line; out.pos = err.pos;
      return false;
    }
    out.instrs = b->prog.n; out.cost = b->prog.cost;
    out.maxPixels = lsf_max_pixels(b->prog, LSF_BUDGET_US);
    const uint32_t pixels = strip.getLengthTotal();
    out.estUs = lsf_estimate_us(b->prog, pixels);
    if (registered && pixels > 0 && out.estUs > LSF_BUDGET_US) {   // at boot the strip is not set up yet: the per-frame check covers it
      snprintf(out.msg, sizeof(out.msg), "over budget: ~%lu ms for %lu LEDs, this formula fits %lu LEDs",
               (unsigned long)((out.estUs + 999) / 1000), (unsigned long)pixels, (unsigned long)out.maxPixels);
      return false;
    }
    out.ok = true;
    if (dry) return true;
    LsfSlot& s = g_slots[idx];
    if (++g_installGen == 0) g_installGen = 1;
    b->resetStatus(g_installGen);
    memcpy(s.src, src, len); s.src[len] = 0; s.srcLen = (uint16_t)len;
    s.cfgErr[0] = 0;
    if (name) setName(s, idx, name);
    g_store.publish(idx, b);   // from the next segment frame on, the renderer draws with the new program
    return true;
  }

  void clearSlot(uint8_t idx) {
    LsfSlot& s = g_slots[idx];
    g_store.publish(idx, nullptr);
    s.srcLen = 0; s.src[0] = 0; s.cfgErr[0] = 0;
    setName(s, idx, nullptr);
  }

  // A source from cfg.json: the "formula" key holds the stored form (lsfStoreDecode), the "src" key of older versions
  // the raw text. Returns nullptr if neither is present (or on an error, then `why` says what), "" for an empty slot.
  // `legacy` is set for "src". A decoded source lives in `*heap`, which the caller frees.
  static const char* configSource(JsonObject e, bool& legacy, char** heap, const char** why) {
    legacy = false; *heap = nullptr; *why = nullptr;
    if (e[F("formula")].is<const char*>()) {
      const size_t cap = LSF_MAX_SRC + 2;   // +1: one byte over the limit still decodes, so the compiler reports it
      char* buf = new (std::nothrow) char[cap];
      if (!buf) { *why = "out of memory"; return nullptr; }
      *heap = buf;
      if (!lsfStoreDecode(e[F("formula")].as<const char*>(), buf, cap)) { *why = "source too long"; return nullptr; }
      return buf;
    }
    if (e[F("src")].is<const char*>()) { legacy = true; return e[F("src")].as<const char*>(); }
    return nullptr;
  }

  // One slot of readFromConfig(): `src` is the configured source (nullptr: none given), `why` a reason it could not be read.
  void applyConfigSlot(uint8_t k, const char* name, const char* src, const char* why) {
    LsfSlot& s = g_slots[k];
    if (why) {
      LsfLast r; r.slot = k;
      snprintf(r.msg, sizeof(r.msg), "stored formula: %s", why);
      if (registered) g_last = r;
      else { snprintf(s.cfgErr, sizeof(s.cfgErr), "%s", r.msg); setName(s, k, name); }
      return;
    }
    if (!src) { if (name) setName(s, k, name); return; }
    if (registered && strcmp(src, s.src) == 0) { if (name) setName(s, k, name); return; }   // unchanged: keep it running
    if (src[0] == 0) {
      if (registered) { clearSlot(k); configNeedsWrite = true; }
      setName(s, k, name);
      return;
    }
    LsfLast r;
    if (install(k, name, src, false, r)) {
      if (registered) g_last = r;
      return;
    }
    if (registered) {
      g_last = r;   // the slot keeps its formula; the settings page shows the old source after the save
    } else {
      // A stored formula that no longer compiles (for example after a limit change) stays visible but inactive.
      setName(s, k, name);
      snprintf(s.cfgErr, sizeof(s.cfgErr), "stored formula: %s", r.msg);
      strlcpy(s.src, src, sizeof(s.src));
      s.srcLen = (uint16_t)strlen(s.src);
    }
  }

 public:
  LsFormulaUsermod() {
    for (uint8_t k = 0; k < LSF_SLOTS; k++) { g_slots[k].src[0] = 0; g_slots[k].cfgErr[0] = 0; g_slots[k].name[0] = 0; g_slots[k].meta[0] = 0; setName(g_slots[k], k, nullptr); }
  }

  void setup() override {
    static_assert(LSF_SLOTS >= 1 && LSF_SLOTS <= 4, "extend the addEffect list below to use more slots");
    g_slots[0].fxId = strip.addEffect(255, &mode_lsf<0>, g_slots[0].meta);
#if LSF_SLOTS > 1
    g_slots[1].fxId = strip.addEffect(255, &mode_lsf<1>, g_slots[1].meta);
#endif
#if LSF_SLOTS > 2
    g_slots[2].fxId = strip.addEffect(255, &mode_lsf<2>, g_slots[2].meta);
#endif
#if LSF_SLOTS > 3
    g_slots[3].fxId = strip.addEffect(255, &mode_lsf<3>, g_slots[3].meta);
#endif
    registered = true;
    initButton();
  }

  void loop() override { pollButton(); }

  // Upload API. POST /json/state with:
  //   {"lsf":{"slot":0,"src":"hsv(x+t/8,255,255)","name":"My Wave"}}   install
  //   {"lsf":{"slot":0,"src":"...","dry":true}}                         validate only
  //   {"lsf":{"slot":0,"clear":true}}                                   remove
  // The outcome is returned in the state JSON under "lsf.last".
  void readFromJsonState(JsonObject& root) override {
    JsonObject o = root[F("lsf")];
    if (o.isNull()) return;
    const int idx = o[F("slot")] | -1;
    if (idx < 0 || idx >= LSF_SLOTS) {
      g_last = LsfLast();
      g_last.slot = idx;
      snprintf(g_last.msg, sizeof(g_last.msg), "slot must be 0..%d", LSF_SLOTS - 1);
      return;
    }
    if (o[F("clear")] | false) {
      clearSlot((uint8_t)idx);
      g_last = LsfLast();
      g_last.slot = idx; g_last.ok = true;
      configNeedsWrite = true;
      return;
    }
    const char* src = o[F("src")];
    if (!src) {
      g_last = LsfLast();
      g_last.slot = idx;
      snprintf(g_last.msg, sizeof(g_last.msg), "src required");
      return;
    }
    const bool dry = o[F("dry")] | false;
    if (install((uint8_t)idx, o[F("name")] | (const char*)nullptr, src, dry, g_last) && !dry) configNeedsWrite = true;
  }

  void addToJsonState(JsonObject& root) override {
    JsonObject o = root.createNestedObject(F("lsf"));
    // Per-slot run-time status comes from the last complete strip frame, so it does not depend on which segment
    // happened to be drawn last.
    LsfFrameGuard<LSF_SLOTS>::FrameStatus fr;
    LsfFrameGuard<LSF_SLOTS>::SlotStatus st[LSF_SLOTS];
    const bool running = g_frame.report(millis(), LSF_STATUS_STALE_MS, &fr, st);
    JsonArray arr = o.createNestedArray(F("slots"));
    for (uint8_t k = 0; k < LSF_SLOTS; k++) {
      const LsfSlot& s = g_slots[k];
      const LsfRun* run = g_store.installed(k);
      const bool disabled = run && run->disabled.load();
      JsonObject e = arr.createNestedObject();
      e[F("slot")] = k;
      e[F("fx")] = s.fxId;
      e[F("name")] = s.name;
      e[F("ok")] = run && !disabled && st[k].over == 0 && st[k].skipped == 0;
      if (run) {
        const LsfProgram& p = run->prog;
        e[F("instrs")] = p.n; e[F("cost")] = p.cost; e[F("prev")] = p.usesPrev;
        e[F("worstUs")] = run->worstUs.load(std::memory_order_relaxed); e[F("avgUs")] = run->avgUs.load(std::memory_order_relaxed);
        e[F("estUs")] = lsf_estimate_us(p, strip.getLengthTotal());   // for the whole strip
        e[F("maxPx")] = lsf_max_pixels(p, LSF_BUDGET_US);
        if (running) { e[F("segs")] = st[k].segs; e[F("skipped")] = st[k].skipped; e[F("overSegs")] = st[k].over; }
      }
      char b[80];
      if (disabled) e[F("err")] = run->err;
      else if (run && st[k].over) {
        snprintf(b, sizeof(b), "over budget: ~%lu ms for a segment of %lu LEDs, max %lu LEDs",
                 (unsigned long)((st[k].overEstUs + 999) / 1000), (unsigned long)st[k].overPixels,
                 (unsigned long)lsf_max_pixels(run->prog, LSF_BUDGET_US));
        e[F("err")] = b;
      } else if (run && st[k].skipped) {
        snprintf(b, sizeof(b), "skipped on %u segment(s): frame budget of %lu ms used up",
                 (unsigned)st[k].skipped, (unsigned long)(LSF_FRAME_BUDGET_US / 1000));
        e[F("err")] = b;
      } else if (s.cfgErr[0]) e[F("err")] = s.cfgErr;
      e[F("src")] = s.src;
    }
    JsonObject l = o.createNestedObject(F("last"));
    l[F("slot")] = g_last.slot; l[F("ok")] = g_last.ok; l[F("dry")] = g_last.dry;
    if (!g_last.ok && g_last.msg[0]) { l[F("err")] = g_last.msg; l[F("line")] = g_last.line; l[F("pos")] = g_last.pos; }
    if (g_last.instrs) { l[F("instrs")] = g_last.instrs; l[F("cost")] = g_last.cost; l[F("estUs")] = g_last.estUs; l[F("maxPx")] = g_last.maxPixels; }
    JsonObject f = o.createNestedObject(F("frame"));
    f[F("segs")] = fr.segs; f[F("estUs")] = fr.estUs; f[F("us")] = fr.realUs; f[F("skipped")] = fr.skipped;
    JsonObject lim = o.createNestedObject(F("limits"));
    lim[F("src")] = LSF_MAX_SRC; lim[F("instrs")] = LSF_MAX_INSNS; lim[F("locals")] = LSF_MAX_LOCALS; lim[F("slots")] = LSF_SLOTS;
    lim[F("budgetUs")] = LSF_BUDGET_US; lim[F("frameBudgetUs")] = LSF_FRAME_BUDGET_US; lim[F("pixels")] = strip.getLengthTotal();
    JsonObject bt = o.createNestedObject(F("button"));
    bt[F("pin")] = btnPin; bt[F("ok")] = btnOwned >= 0; bt[F("stepPct")] = briStepPct;
  }

  void addToConfig(JsonObject& root) override {
    JsonObject top = root.createNestedObject(FPSTR(_name));
    // Sources are saved in their stored form (lsf_runtime.h), which survives WLED's usermod settings page. The buffer
    // is on the heap: this also runs on the web server's task, whose stack is small.
    const size_t cap = 2 * LSF_MAX_SRC + 1;
    char* enc = new (std::nothrow) char[cap];
    for (uint8_t k = 0; k < LSF_SLOTS; k++) {
      char key[8];
      snprintf(key, sizeof(key), "s%u", (unsigned)k);
      JsonObject e = top.createNestedObject(key);
      const LsfSlot& s = g_slots[k];
      e[F("name")] = s.name;
      if (enc && lsfStoreEncode(s.src, s.srcLen, enc, cap) < cap) e[F("formula")] = enc;   // char*: ArduinoJson copies it
      else e[F("src")] = s.src;   // out of memory: the raw form still persists (readFromConfig reads both)
    }
    delete[] enc;
    // Button control: pin -1 switches it off. Changing the pin or the polarity takes effect on save (no reboot).
    top[F("button pin")] = btnPin;
    top[F("button active low")] = btnActiveLow;
    top[F("brightness step")] = briStepPct;
  }

  // The settings page shows each formula in its stored form; say how to read it.
  void appendConfigData(Print& settingsScript) override {
    for (uint8_t k = 0; k < LSF_SLOTS; k++)
      settingsScript.printf_P(PSTR("addInfo('%s:s%u:formula',1,'<br><small>\\\\n = new line, \\\\q = &quot;, \\\\a = &amp;, \\\\\\\\ = \\\\; or use tools/lsfx.py</small>');"),
                              _name, (unsigned)k);
  }

  // Called at boot (before setup()) with cfg.json, and at run time when the usermod settings page is saved or
  // /json/cfg is posted. At run time a slot is recompiled only when its source actually changed, so saving the
  // settings page (for the button, say) neither restarts the formulas nor re-enables one the slow-frame guard
  // switched off; a changed source that does not compile leaves the slot as it was and is reported in lsf.last.
  bool readFromConfig(JsonObject& root) override {
    JsonObject top = root[FPSTR(_name)];
    if (top.isNull()) return false;   // first boot: let WLED write the defaults
    bool complete = true;
    for (uint8_t k = 0; k < LSF_SLOTS; k++) {
      char key[8];
      snprintf(key, sizeof(key), "s%u", (unsigned)k);
      JsonObject e = top[key];
      if (e.isNull()) continue;
      const char* name = e[F("name")] | (const char*)nullptr;
      bool legacy;
      char* heap;
      const char* why;
      const char* src = configSource(e, legacy, &heap, &why);
      if (legacy) complete = false;   // rewrite cfg.json in the current format
      applyConfigSlot(k, name, src, why);
      delete[] heap;
    }

    // Button settings. A config saved before this feature existed lacks the keys: `complete` is then false, so WLED
    // writes the defaults back and the settings page shows them.
    int pin = -1;
    complete &= getJsonValue(top[F("button pin")], pin, -1);
    complete &= getJsonValue(top[F("button active low")], btnActiveLow, true);
    int step = 5;
    complete &= getJsonValue(top[F("brightness step")], step, 5);
    briStepPct = (uint8_t)constrain(step, 1, 50);
    const int8_t newPin = (pin >= 0 && pin < 255) ? (int8_t)pin : -1;
    btnPin = newPin;
    if (registered) initButton();   // saved from the settings page while running: apply pin and polarity now
    return complete;
  }

  uint16_t getId() override { return USERMOD_ID_UNSPECIFIED; }
};

const char LsFormulaUsermod::_name[] PROGMEM = "ls_formula";

static LsFormulaUsermod ls_formula_usermod;
REGISTER_USERMOD(ls_formula_usermod);

#endif  // ARDUINO_ARCH_ESP32
