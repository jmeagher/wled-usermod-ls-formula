// One-button control for local-only displays: the pure logic, with no WLED or Arduino dependency
// (so tests/test_button.cpp can run it on the desktop).
//
//   click        -> next preset
//   double click -> power on / off
//   hold         -> brightness steps up every LSF_BTN_STEP_MS while the button is down, wrapping from the top
//                   back to the lowest step; releasing the button stops it
//
// ls_formula.cpp feeds this the pin level and acts on the events.
#pragma once
#include <stdint.h>

#ifndef LSF_BTN_DEBOUNCE_MS
#define LSF_BTN_DEBOUNCE_MS 30    // a level change is ignored for this long after the previous one
#endif
#ifndef LSF_BTN_DOUBLE_MS
#define LSF_BTN_DOUBLE_MS 350     // second press must start within this long after the first release
#endif
#ifndef LSF_BTN_HOLD_MS
#define LSF_BTN_HOLD_MS 500       // pressed this long = a hold
#endif
#ifndef LSF_BTN_STEP_MS
#define LSF_BTN_STEP_MS 250       // time between brightness steps while held
#endif

enum LsfBtnEvent : uint8_t {
  LSFB_NONE = 0,
  LSFB_CLICK,       // one short press (reported once the double-click window has passed)
  LSFB_DOUBLE,      // two short presses
  LSFB_HOLD_STEP,   // the button has been held: do one brightness step (first one when the hold is recognised)
  LSFB_HOLD_END     // a hold was released
};

// Call poll() often (every loop pass) with the debounced-by-us raw state and a millisecond clock.
// It returns at most one event per call; time comparisons are wrap-safe for a 32-bit millis().
class LsfButton {
 public:
  LsfBtnEvent poll(bool pressed, uint32_t now) {
    if (!started_) {
      started_ = true;
      edgeAt_ = now - LSF_BTN_DEBOUNCE_MS;
      waitRelease_ = pressed;   // held while powering up (e.g. the BOOT button): not a gesture, wait for the release
    }
    if (waitRelease_) {
      if (pressed) return LSFB_NONE;
      waitRelease_ = false;
      edgeAt_ = now;
    }

    // A lone click is only a click once no second press can follow.
    if (!down_ && clicks_ && (uint32_t)(now - upAt_) >= LSF_BTN_DOUBLE_MS) { clicks_ = 0; return LSFB_CLICK; }

    if (pressed != down_ && (uint32_t)(now - edgeAt_) >= LSF_BTN_DEBOUNCE_MS) {
      edgeAt_ = now;
      down_ = pressed;
      if (down_) {
        downAt_ = now;
        holding_ = false;
      } else if (holding_) {
        holding_ = false;
        clicks_ = 0;
        return LSFB_HOLD_END;
      } else if (clicks_) {
        clicks_ = 0;
        return LSFB_DOUBLE;
      } else {
        clicks_ = 1;
        upAt_ = now;
      }
    }

    if (down_) {
      if (!holding_ && (uint32_t)(now - downAt_) >= LSF_BTN_HOLD_MS) {
        holding_ = true;
        clicks_ = 0;            // a press that turns into a hold cancels a pending click
        stepAt_ = now;
        return LSFB_HOLD_STEP;
      }
      if (holding_ && (uint32_t)(now - stepAt_) >= LSF_BTN_STEP_MS) {
        stepAt_ = now;
        return LSFB_HOLD_STEP;
      }
    }
    return LSFB_NONE;
  }

  bool holding() const { return holding_; }
  void reset() { *this = LsfButton(); }

 private:
  bool started_ = false, waitRelease_ = false, down_ = false, holding_ = false;
  uint8_t clicks_ = 0;
  uint32_t edgeAt_ = 0, downAt_ = 0, upAt_ = 0, stepAt_ = 0;
};

// Next brightness in `stepPct` percent steps: 5% -> 10% -> ... -> 100% -> back to the first step. From off (0) the
// first step is returned. The result is never 0 (that would be "off") and 100% is exactly 255.
inline uint8_t lsfNextBrightness(uint8_t bri, uint8_t stepPct) {
  if (stepPct < 1) stepPct = 1;
  if (stepPct > 50) stepPct = 50;
  const unsigned pct = bri == 0 ? 0 : (unsigned(bri) * 100 + 127) / 255;   // nearest percent
  unsigned next = (pct / stepPct + 1) * stepPct;                            // the step above the current one
  if (next > 100) next = stepPct;
  const unsigned v = (next * 255 + 50) / 100;
  return uint8_t(v < 1 ? 1 : v > 255 ? 255 : v);
}

// The preset ids (1..250) that exist, kept as a bitmap so "the next one after N" is cheap and always in order.
class LsfIdSet {
 public:
  static const uint8_t kMax = 250;
  void clear() { for (uint8_t k = 0; k < 8; k++) w_[k] = 0; }
  void add(unsigned id) { if (id >= 1 && id <= kMax) w_[id >> 5] |= 1u << (id & 31); }
  bool has(unsigned id) const { return id >= 1 && id <= kMax && (w_[id >> 5] >> (id & 31) & 1u); }
  unsigned count() const { unsigned n = 0; for (unsigned id = 1; id <= kMax; id++) n += has(id); return n; }
  // The smallest id greater than `cur`, wrapping to the smallest id overall; 0 when the set is empty.
  unsigned next(unsigned cur) const {
    for (unsigned id = cur + 1; id <= kMax; id++) if (has(id)) return id;
    for (unsigned id = 1; id <= kMax; id++) if (has(id)) return id;
    return 0;
  }
 private:
  uint32_t w_[8] = {0, 0, 0, 0, 0, 0, 0, 0};
};
