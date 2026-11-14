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

#ifndef LSF_SLOTS
#define LSF_SLOTS 4
#endif
#ifndef LSF_SLOW_FRAME_US
#define LSF_SLOW_FRAME_US 60000   // a frame this slow, three times in a row, disables the slot
#endif
#ifndef LSF_BUDGET_US
#define LSF_BUDGET_US 15000       // largest estimated render time per frame for one formula (see lsf_estimate_us)
#endif
#define LSF_NAME_MAX 20

struct LsfSlot {
  LsfProgram prog;
  bool       ok = false;         // a valid program is installed
  bool       disabled = false;   // switched off by the slow-frame guard
  bool       overBudget = false; // estimated frame time exceeds LSF_BUDGET_US for the current segment size
  uint32_t   estUs = 0;          // estimate for the segment last rendered
  uint8_t    fxId = 255;         // WLED effect id once registered
  uint8_t    slowFrames = 0;
  uint32_t   worstUs = 0;
  uint32_t   avgUs = 0;          // exponential moving average of the real frame time (worstUs includes preemption by WiFi/HTTP)
  uint16_t   srcLen = 0;
  char       name[LSF_NAME_MAX + 1];
  char       src[LSF_MAX_SRC + 1];
  char       meta[96];           // WLED effect metadata string, rebuilt when the name changes
  char       runtimeErr[40];
};

static LsfSlot g_slots[LSF_SLOTS];

// Result of the most recent upload or dry run, reported in the state JSON.
static struct {
  int      slot = -1;
  bool     ok = false;
  bool     dry = false;
  char     msg[72] = "";
  uint16_t line = 0, pos = 0, instrs = 0, cost = 0;
  uint32_t estUs = 0, maxPixels = 0;
} g_last;

static void buildMeta(LsfSlot& s) {
  // Format: Name@slider labels;colour labels;palette;dimension flags;defaults
  // "12" = usable on 1D and 2D segments. See https://kno.wled.ge/interfaces/json-api/#effect-metadata
  snprintf(s.meta, sizeof(s.meta), "%s@Speed,Intensity,P1,P2,P3;!,!,!;!;12;sx=128,ix=128", s.name);
}

static void setName(LsfSlot& s, uint8_t idx, const char* wanted) {
  size_t o = 0;
  if (wanted) {
    for (; *wanted && o < LSF_NAME_MAX; wanted++) {   // the metadata format reserves '@' ';' ','
      char c = *wanted;
      if ((c >= 'a' && c <= 'z') || (c >= 'A' && c <= 'Z') || (c >= '0' && c <= '9') || c == ' ' || c == '-' || c == '_') s.name[o++] = c;
    }
  }
  s.name[o] = 0;
  if (o == 0) snprintf(s.name, sizeof(s.name), "LS Formula %u", (unsigned)idx + 1);
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
  LsfSlot& sl = g_slots[s];
  if (!sl.ok || sl.disabled) { SEGMENT.fill(BLACK); return; }

  const bool two = strip.isMatrix && SEGMENT.is2D();
  const int w = two ? (int)SEG_W : (int)SEGLEN;
  const int h = two ? (int)SEG_H : 1;
  const unsigned total = (unsigned)w * (unsigned)h;
  if (total == 0) return;

  // Admission control, every frame: an LED-count or segment change can make an accepted formula too slow.
  sl.estUs = lsf_estimate_us(sl.prog, total);
  sl.overBudget = sl.estUs > LSF_BUDGET_US;
  if (sl.overBudget) { SEGMENT.fill(BLACK); return; }

  RenderState rs = { nullptr, w, h, total };
  uint8_t* next = nullptr;
  if (sl.prog.usesPrev) {
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
      const uint32_t col = lsf_run(sl.prog, c);
      if (next) {
        uint8_t* q = next + (size_t)i * 3;
        q[0] = col >> 16; q[1] = col >> 8; q[2] = col;
      }
      if (two) SEGMENT.setPixelColorXY(x, y, col); else SEGMENT.setPixelColor(i, col);
    }
  }
  if (next) memcpy(rs.prev, next, (size_t)total * 3);

  // Slow-frame guard: a formula that cannot keep up must not stall the strip service loop (and WiFi with it).
  const uint32_t us = micros() - t0;
  if (us > sl.worstUs) sl.worstUs = us;
  sl.avgUs = sl.avgUs ? (sl.avgUs * 15 + us) / 16 : us;
  if (us > LSF_SLOW_FRAME_US) {
    if (++sl.slowFrames >= 3) {
      sl.disabled = true;
      snprintf(sl.runtimeErr, sizeof(sl.runtimeErr), "disabled: frame took %lu ms", (unsigned long)(us / 1000));
    }
  } else {
    sl.slowFrames = 0;
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

  void stepBrightness(bool up) {
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
      if (v == bri) return;                                // already at the lowest step
      bri = v;
    }
    stateUpdated(CALL_MODE_BUTTON);
  }

  void pollButton() {
    if (btnOwned < 0) return;
    const bool pressed = (digitalRead(btnOwned) == LOW) == btnActiveLow;
    switch (btn.poll(pressed, millis())) {
      case LSFB_CLICK:     nextPreset(); break;
      case LSFB_DOUBLE:    toggleOnOff(); stateUpdated(CALL_MODE_BUTTON); break;
      case LSFB_HOLD_UP:   stepBrightness(true); break;
      case LSFB_HOLD_DOWN: stepBrightness(false); break;
      default: break;
    }
  }

  // Compile `src` and, unless `dry`, install it in slot `idx`. Returns true on success. Always fills g_last.
  bool install(uint8_t idx, const char* name, const char* src, bool dry) {
    g_last.slot = idx; g_last.dry = dry; g_last.ok = false;
    g_last.line = g_last.pos = g_last.instrs = g_last.cost = 0;
    g_last.estUs = g_last.maxPixels = 0;
    snprintf(g_last.msg, sizeof(g_last.msg), "%s", "");
    if (idx >= LSF_SLOTS) { snprintf(g_last.msg, sizeof(g_last.msg), "slot out of range"); return false; }
    const size_t len = strlen(src);
    LsfProgram* tmp = new (std::nothrow) LsfProgram;   // off the (small) async_tcp stack
    if (!tmp) { snprintf(g_last.msg, sizeof(g_last.msg), "out of memory"); return false; }
    LsfError err;
    const bool ok = lsf_compile(src, len, tmp, &err);
    if (!ok) {
      snprintf(g_last.msg, sizeof(g_last.msg), "%s", err.msg);
      g_last.line = err.line; g_last.pos = err.pos;
      delete tmp;
      return false;
    }
    g_last.instrs = tmp->n; g_last.cost = tmp->cost;
    g_last.maxPixels = lsf_max_pixels(*tmp, LSF_BUDGET_US);
    const uint32_t pixels = strip.getLengthTotal();
    g_last.estUs = lsf_estimate_us(*tmp, pixels);
    if (registered && pixels > 0 && g_last.estUs > LSF_BUDGET_US) {   // at boot the strip is not set up yet: the per-frame check covers it
      snprintf(g_last.msg, sizeof(g_last.msg), "over budget: ~%lu ms for %lu LEDs, this formula fits %lu LEDs",
               (unsigned long)((g_last.estUs + 999) / 1000), (unsigned long)pixels, (unsigned long)g_last.maxPixels);
      delete tmp;
      return false;
    }
    g_last.ok = true;
    if (!dry) {
      LsfSlot& s = g_slots[idx];
      const bool live = registered;
      if (live) { strip.suspend(); strip.waitForIt(); }   // no frame may be mid-render while the program is swapped
      memcpy(&s.prog, tmp, sizeof(LsfProgram));
      memcpy(s.src, src, len); s.src[len] = 0; s.srcLen = (uint16_t)len;
      s.ok = true; s.disabled = false; s.overBudget = false; s.slowFrames = 0; s.worstUs = 0; s.avgUs = 0; s.runtimeErr[0] = 0;
      if (name) setName(s, idx, name);
      if (live) strip.resume();
    }
    delete tmp;
    return true;
  }

  void clearSlot(uint8_t idx) {
    LsfSlot& s = g_slots[idx];
    const bool live = registered;
    if (live) { strip.suspend(); strip.waitForIt(); }
    s.ok = false; s.disabled = false; s.srcLen = 0; s.src[0] = 0; s.runtimeErr[0] = 0;
    setName(s, idx, nullptr);
    if (live) strip.resume();
  }

 public:
  LsFormulaUsermod() {
    for (uint8_t k = 0; k < LSF_SLOTS; k++) { g_slots[k].src[0] = 0; g_slots[k].runtimeErr[0] = 0; setName(g_slots[k], k, nullptr); }
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
      g_last.slot = idx; g_last.ok = false; g_last.dry = false;
      snprintf(g_last.msg, sizeof(g_last.msg), "slot must be 0..%d", LSF_SLOTS - 1);
      return;
    }
    if (o[F("clear")] | false) {
      clearSlot((uint8_t)idx);
      g_last.slot = idx; g_last.ok = true; g_last.dry = false; g_last.msg[0] = 0;
      g_last.line = g_last.pos = g_last.instrs = g_last.cost = 0;
      configNeedsWrite = true;
      return;
    }
    const char* src = o[F("src")];
    if (!src) {
      g_last.slot = idx; g_last.ok = false; g_last.dry = false;
      snprintf(g_last.msg, sizeof(g_last.msg), "src required");
      return;
    }
    const bool dry = o[F("dry")] | false;
    if (install((uint8_t)idx, o[F("name")] | (const char*)nullptr, src, dry) && !dry) configNeedsWrite = true;
  }

  void addToJsonState(JsonObject& root) override {
    JsonObject o = root.createNestedObject(F("lsf"));
    JsonArray arr = o.createNestedArray(F("slots"));
    for (uint8_t k = 0; k < LSF_SLOTS; k++) {
      const LsfSlot& s = g_slots[k];
      JsonObject e = arr.createNestedObject();
      e[F("slot")] = k;
      e[F("fx")] = s.fxId;
      e[F("name")] = s.name;
      e[F("ok")] = s.ok && !s.disabled && !s.overBudget;
      if (s.ok) {
        e[F("instrs")] = s.prog.n; e[F("cost")] = s.prog.cost; e[F("prev")] = s.prog.usesPrev; e[F("worstUs")] = s.worstUs; e[F("avgUs")] = s.avgUs;
        e[F("estUs")] = lsf_estimate_us(s.prog, strip.getLengthTotal());   // for the whole strip
        e[F("maxPx")] = lsf_max_pixels(s.prog, LSF_BUDGET_US);
      }
      if (s.overBudget) {
        char b[72];
        snprintf(b, sizeof(b), "over budget: ~%lu ms for this segment, max %lu LEDs", (unsigned long)((s.estUs + 999) / 1000), (unsigned long)lsf_max_pixels(s.prog, LSF_BUDGET_US));
        e[F("err")] = b;
      } else if (s.runtimeErr[0]) e[F("err")] = s.runtimeErr;
      e[F("src")] = s.src;
    }
    JsonObject l = o.createNestedObject(F("last"));
    l[F("slot")] = g_last.slot; l[F("ok")] = g_last.ok; l[F("dry")] = g_last.dry;
    if (!g_last.ok && g_last.msg[0]) { l[F("err")] = g_last.msg; l[F("line")] = g_last.line; l[F("pos")] = g_last.pos; }
    if (g_last.instrs) { l[F("instrs")] = g_last.instrs; l[F("cost")] = g_last.cost; l[F("estUs")] = g_last.estUs; l[F("maxPx")] = g_last.maxPixels; }
    JsonObject lim = o.createNestedObject(F("limits"));
    lim[F("src")] = LSF_MAX_SRC; lim[F("instrs")] = LSF_MAX_INSNS; lim[F("locals")] = LSF_MAX_LOCALS; lim[F("slots")] = LSF_SLOTS;
    lim[F("budgetUs")] = LSF_BUDGET_US; lim[F("pixels")] = strip.getLengthTotal();
    JsonObject bt = o.createNestedObject(F("button"));
    bt[F("pin")] = btnPin; bt[F("ok")] = btnOwned >= 0; bt[F("stepPct")] = briStepPct;
  }

  void addToConfig(JsonObject& root) override {
    JsonObject top = root.createNestedObject(FPSTR(_name));
    for (uint8_t k = 0; k < LSF_SLOTS; k++) {
      char key[8];
      snprintf(key, sizeof(key), "s%u", (unsigned)k);
      JsonObject e = top.createNestedObject(key);
      e[F("name")] = g_slots[k].name;
      e[F("src")] = g_slots[k].src;
    }
    // Button control: pin -1 switches it off. Changing the pin or the polarity takes effect on save (no reboot).
    top[F("button pin")] = btnPin;
    top[F("button active low")] = btnActiveLow;
    top[F("brightness step")] = briStepPct;
  }

  bool readFromConfig(JsonObject& root) override {
    JsonObject top = root[FPSTR(_name)];
    if (top.isNull()) return false;   // first boot: let WLED write the defaults
    for (uint8_t k = 0; k < LSF_SLOTS; k++) {
      char key[8];
      snprintf(key, sizeof(key), "s%u", (unsigned)k);
      JsonObject e = top[key];
      if (e.isNull()) continue;
      const char* src = e[F("src")] | "";
      const char* name = e[F("name")] | (const char*)nullptr;
      if (src[0] == 0) { setName(g_slots[k], k, name); continue; }
      if (!install(k, name, src, false)) {
        // A stored formula that no longer compiles (for example after a limit change) stays visible but inactive.
        setName(g_slots[k], k, name);
        snprintf(g_slots[k].runtimeErr, sizeof(g_slots[k].runtimeErr), "stored formula: %s", g_last.msg);
        strlcpy(g_slots[k].src, src, sizeof(g_slots[k].src));
        g_slots[k].srcLen = (uint16_t)strlen(g_slots[k].src);
      }
    }

    // Button settings. A config saved before this feature existed lacks the keys: `complete` is then false, so WLED
    // writes the defaults back and the settings page shows them.
    bool complete = true;
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
