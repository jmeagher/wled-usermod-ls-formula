// Host tests for lsf_button.h: the gesture state machine, the brightness steps and the preset id set.
#include "../lsf_button.h"

#include <cstdio>
#include <cstdlib>
#include <string>
#include <utility>
#include <vector>

static int failures = 0;
#define CHECK(cond) do { if (!(cond)) { std::printf("FAIL %s:%d  %s\n", __FILE__, __LINE__, #cond); failures++; } } while (0)
#define CHECK_EQ(a, b) do { long long x_ = (a), y_ = (b); if (x_ != y_) { \
  std::printf("FAIL %s:%d  %s == %s  (%lld vs %lld)\n", __FILE__, __LINE__, #a, #b, x_, y_); failures++; } } while (0)

struct Ev { uint32_t at; LsfBtnEvent e; };

// `levels` = (time, pressed) changes in order; the button is polled every `pollMs` until `endMs`. `t0` offsets the clock.
static std::vector<Ev> run(const std::vector<std::pair<uint32_t, bool>>& levels, uint32_t endMs, uint32_t t0 = 1000,
                           bool initial = false, uint32_t pollMs = 1) {
  LsfButton b;
  std::vector<Ev> out;
  size_t k = 0;
  bool level = initial;
  for (uint32_t t = 0; t <= endMs; t += pollMs) {
    while (k < levels.size() && levels[k].first <= t) level = levels[k++].second;
    LsfBtnEvent e = b.poll(level, t0 + t);
    if (e != LSFB_NONE) out.push_back({t, e});
  }
  return out;
}
static int count(const std::vector<Ev>& v, LsfBtnEvent e) { int n = 0; for (auto& x : v) n += x.e == e; return n; }

static void testClick() {
  auto ev = run({{100, true}, {180, false}}, 2000);
  CHECK_EQ(ev.size(), 1u);
  CHECK(ev[0].e == LSFB_CLICK);
  CHECK(ev[0].at >= 180 + LSF_BTN_DOUBLE_MS);          // only reported once a second press can no longer follow
  CHECK(ev[0].at < 180 + LSF_BTN_DOUBLE_MS + 5);
}

static void testDouble() {
  auto ev = run({{100, true}, {160, false}, {300, true}, {360, false}}, 2000);
  CHECK_EQ(ev.size(), 1u);
  CHECK(ev[0].e == LSFB_DOUBLE);
  CHECK_EQ(ev[0].at, 360);                              // immediately on the second release
}

static void testTwoSlowClicksAreTwoClicks() {
  auto ev = run({{100, true}, {160, false}, {900, true}, {960, false}}, 3000);
  CHECK_EQ(count(ev, LSFB_CLICK), 2);
  CHECK_EQ(count(ev, LSFB_DOUBLE), 0);
}

static void testTripleIsDoublePlusClick() {
  auto ev = run({{100, true}, {160, false}, {300, true}, {360, false}, {500, true}, {560, false}}, 3000);
  CHECK_EQ(count(ev, LSFB_DOUBLE), 1);
  CHECK_EQ(count(ev, LSFB_CLICK), 1);
}

static void testHoldStepsThenStopsOnRelease() {
  auto ev = run({{100, true}, {2000, false}}, 4000);   // held 1.9 s
  CHECK_EQ(count(ev, LSFB_CLICK), 0);
  CHECK_EQ(count(ev, LSFB_DOUBLE), 0);
  CHECK_EQ(count(ev, LSFB_HOLD_END), 1);
  CHECK(ev.back().e == LSFB_HOLD_END);
  CHECK_EQ(ev.back().at, 2000);
  int steps = count(ev, LSFB_HOLD_UP);
  // first step at 500 ms, then one per 250 ms: 500, 750, ..., 1750 (+ possibly 2000 is the release instead)
  CHECK_EQ(steps, 6);
  CHECK_EQ(ev[0].at, 100 + LSF_BTN_HOLD_MS);
  CHECK_EQ(ev[1].at - ev[0].at, LSF_BTN_STEP_MS);
  for (auto& x : ev) CHECK(x.at <= 2000);              // nothing after the release
}

static void testShortPressJustUnderHoldIsClick() {
  auto ev = run({{100, true}, {100 + LSF_BTN_HOLD_MS - 20, false}}, 2000);
  CHECK_EQ(count(ev, LSFB_CLICK), 1);
  CHECK_EQ(count(ev, LSFB_HOLD_UP), 0);
}

static void testDoubleClickAndHoldDims() {
  // press, release, press again and keep holding: steps DOWN, no click, no double click, and release stops it
  auto ev = run({{100, true}, {160, false}, {300, true}, {2000, false}}, 4000);
  CHECK_EQ(count(ev, LSFB_CLICK), 0);
  CHECK_EQ(count(ev, LSFB_DOUBLE), 0);
  CHECK_EQ(count(ev, LSFB_HOLD_UP), 0);
  CHECK_EQ(count(ev, LSFB_HOLD_END), 1);
  CHECK_EQ(count(ev, LSFB_HOLD_DOWN), 5);              // at 800, 1050, 1300, 1550, 1800; the release at 2000 stops it
  CHECK_EQ(ev[0].at, 300 + LSF_BTN_HOLD_MS);
  CHECK_EQ(ev[1].at - ev[0].at, LSF_BTN_STEP_MS);
  CHECK(ev.back().e == LSFB_HOLD_END);
  CHECK_EQ(ev.back().at, 2000);
}

static void testSingleHoldAfterAnIdleGapStillBrightens() {
  // a press long after an earlier click is a fresh single hold (up), not a "second press"
  auto ev = run({{100, true}, {160, false}, {900, true}, {2000, false}}, 4000);
  CHECK_EQ(count(ev, LSFB_CLICK), 1);
  CHECK(count(ev, LSFB_HOLD_UP) >= 3);
  CHECK_EQ(count(ev, LSFB_HOLD_DOWN), 0);
}

static void testDimThenUpDirectionsDoNotLeak() {
  // after a dimming hold, the next plain hold brightens again
  auto ev = run({{100, true}, {160, false}, {300, true}, {1500, false}, {2500, true}, {3600, false}}, 6000);
  CHECK(count(ev, LSFB_HOLD_DOWN) >= 3);
  CHECK(count(ev, LSFB_HOLD_UP) >= 3);
  CHECK_EQ(count(ev, LSFB_HOLD_END), 2);
}

static void testBounceIsIgnored() {
  // contact bounce on press and release (edges 5 ms apart) must still be one click
  auto ev = run({{100, true}, {105, false}, {110, true}, {200, false}, {205, true}, {210, false}}, 2000);
  CHECK_EQ(ev.size(), 1u);
  CHECK(ev[0].e == LSFB_CLICK);
}

static void testHeldAtBootIsIgnoredUntilReleased() {
  auto ev = run({{3000, false}}, 6000, 1000, true);    // pressed from the start, released at 3 s
  CHECK_EQ(ev.size(), 0u);
  auto ev2 = run({{3000, false}, {3500, true}, {3560, false}}, 6000, 1000, true);   // then a normal click works
  CHECK_EQ(ev2.size(), 1u);
  CHECK(ev2[0].e == LSFB_CLICK);
}

static void testClockWrap() {
  // millis() wraps at 2^32; a double click across the wrap must still be a double click
  const uint32_t t0 = 0xFFFFFFFFu - 200;
  auto ev = run({{100, true}, {160, false}, {300, true}, {360, false}}, 2000, t0);
  CHECK_EQ(count(ev, LSFB_DOUBLE), 1);
  auto hold = run({{100, true}, {1500, false}}, 3000, t0);
  CHECK(count(hold, LSFB_HOLD_UP) >= 3);
  CHECK_EQ(count(hold, LSFB_HOLD_END), 1);
}

static void testSlowPolling() {
  // a busy main loop polling every 20 ms still gets the right gestures
  auto ev = run({{100, true}, {200, false}, {400, true}, {500, false}}, 3000, 1000, false, 20);
  CHECK_EQ(count(ev, LSFB_DOUBLE), 1);
  auto hold = run({{100, true}, {1700, false}}, 3000, 1000, false, 20);
  CHECK(count(hold, LSFB_HOLD_UP) >= 4);
  CHECK_EQ(count(hold, LSFB_HOLD_END), 1);
}

static void testBrightnessSteps() {
  CHECK_EQ(lsfNextBrightness(0, 5), 3);                // from off: the lowest level, 1 %
  CHECK_EQ(lsfNextBrightness(3, 5), 5);                // 1 % -> 2 %
  CHECK_EQ(lsfNextBrightness(5, 5), 8);                // 2 % -> 3 %
  CHECK_EQ(lsfNextBrightness(8, 5), 10);               // 3 % -> 4 %
  CHECK_EQ(lsfNextBrightness(10, 5), 13);              // 4 % -> 5 %
  CHECK_EQ(lsfNextBrightness(13, 5), 26);              // 5 % -> 10 %
  CHECK_EQ(lsfNextBrightness(128, 5), 140);            // 50 % -> 55 %
  CHECK_EQ(lsfNextBrightness(242, 5), 255);            // 95 % -> 100 %
  CHECK_EQ(lsfNextBrightness(255, 5), 3);              // wraps to 1 %
  // 24 levels (1, 2, 3, 4, 5, 10, ... 100 %): 24 presses from the lowest come back around
  unsigned b = 3; int n = 0;
  do { b = lsfNextBrightness(uint8_t(b), 5); n++; } while (b != 3 && n < 100);
  CHECK_EQ(n, 24);
  // different step sizes: the 1..4 % levels exist only below the first step
  CHECK_EQ(lsfNextBrightness(3, 10), 5);   CHECK_EQ(lsfNextBrightness(10, 10), 26);   // 1 -> 2 %, 4 % -> 10 %
  CHECK_EQ(lsfNextBrightness(8, 3), 15);   // step 3: levels 1, 2, 3, 6, 9, ...: 3 % -> 6 %
  CHECK_EQ(lsfNextBrightness(252, 3), 255); // 99 % -> 100 % even though 3 does not divide 100
  CHECK_EQ(lsfNextBrightness(0, 1), 3);    CHECK_EQ(lsfNextBrightness(3, 1), 5);          // step 1: 1, 2, 3 ...
  const uint8_t steps[] = {1, 3, 5, 10, 25, 50, 0, 200};
  for (uint8_t st : steps)
    for (unsigned v = 0; v <= 255; v++) {
      unsigned r = lsfNextBrightness(uint8_t(v), st);
      CHECK(r >= 1);
      CHECK(r != v || v == 255 || v == 0);             // always moves (the single value that cannot is none)
    }
}

static void testBrightnessDown() {
  CHECK_EQ(lsfPrevBrightness(255, 5), 242);            // 100 % -> 95 %
  CHECK_EQ(lsfPrevBrightness(128, 5), 115);            // 50 % -> 45 %
  CHECK_EQ(lsfPrevBrightness(26, 5), 13);              // 10 % -> 5 %
  CHECK_EQ(lsfPrevBrightness(13, 5), 10);              // 5 % -> 4 %
  CHECK_EQ(lsfPrevBrightness(10, 5), 8);               // 4 % -> 3 %
  CHECK_EQ(lsfPrevBrightness(8, 5), 5);                // 3 % -> 2 %
  CHECK_EQ(lsfPrevBrightness(5, 5), 3);                // 2 % -> 1 %
  CHECK_EQ(lsfPrevBrightness(3, 5), 3);                // stays at 1 %, never off
  CHECK_EQ(lsfPrevBrightness(1, 5), 3);                // below 1 %: 1 %
  CHECK_EQ(lsfPrevBrightness(0, 5), 0);                // off stays off
  CHECK_EQ(lsfPrevBrightness(130, 5), 128);            // 51 %: the level below it is 50 %
  CHECK_EQ(lsfPrevBrightness(26, 10), 10);             // step 10: 10 % -> 4 % (no 5..9 %)
  // walking down from 100 % reaches 1 % in 23 presses, strictly decreasing
  unsigned b = 255; int n = 0;
  while (lsfPrevBrightness(uint8_t(b), 5) < b) { b = lsfPrevBrightness(uint8_t(b), 5); n++; }
  CHECK_EQ(n, 23);
  CHECK_EQ(b, 3);
  // down is the exact inverse of up on every level, for several step sizes
  const uint8_t steps[] = {1, 3, 5, 10, 50};
  for (uint8_t st : steps) {
    unsigned v = lsfNextBrightness(0, st);             // 1 %
    for (int k = 0; k < 200 && v != 255; k++) {
      unsigned up = lsfNextBrightness(uint8_t(v), st);
      CHECK(up > v);
      CHECK_EQ(lsfPrevBrightness(uint8_t(up), st), v);
      v = up;
    }
    CHECK_EQ(v, 255);
  }
  const uint8_t odd[] = {0, 200};
  for (uint8_t st : odd)
    for (unsigned v = 0; v <= 255; v++) {
      unsigned r = lsfPrevBrightness(uint8_t(v), st);
      CHECK((v == 0 && r == 0) || (v > 0 && r >= 1 && r <= 255));
    }
}

static void testBrightnessEnds() {
  CHECK(lsfAtBrightnessEnd(255));                      // 100 %
  CHECK(lsfAtBrightnessEnd(3));                        // 1 %
  CHECK(lsfAtBrightnessEnd(1));
  CHECK(!lsfAtBrightnessEnd(0));                       // off is not a level
  CHECK(!lsfAtBrightnessEnd(5));                       // 2 %
  CHECK(!lsfAtBrightnessEnd(242));                     // 95 %
  CHECK(!lsfAtBrightnessEnd(128));
}

// A held button where the "app" pauses on the ends, like ls_formula.cpp does.
static void testHoldPausesAtTheEnds() {
  LsfButton b;
  uint8_t bri = 242;                                   // 95 %: one step below the top
  std::vector<std::pair<uint32_t, unsigned>> seen;     // (time, brightness after the step)
  for (uint32_t t = 0; t <= 8000; t++) {
    const bool pressed = t >= 100 && t < 7000;
    LsfBtnEvent e = b.poll(pressed, 5000 + t);
    if (e == LSFB_HOLD_UP) {
      bri = lsfNextBrightness(bri, 5);
      seen.push_back({t, bri});
      if (lsfAtBrightnessEnd(bri)) b.delayNextStep(5000 + t, LSF_BTN_END_HOLD_MS);
    }
  }
  // 600: 95 -> 100 %  (pause 1.5 s)  ->  2100: wraps to 1 % (pause 1.5 s)  ->  3600: 2 %  ->  3850: 3 %
  CHECK(seen.size() >= 5);
  CHECK_EQ(seen[0].second, 255);
  CHECK_EQ(seen[1].first - seen[0].first, LSF_BTN_END_HOLD_MS);   // held at 100 %
  CHECK_EQ(seen[1].second, 3);                                    // then wrapped to 1 %
  CHECK_EQ(seen[2].first - seen[1].first, LSF_BTN_END_HOLD_MS);   // held at 1 %
  CHECK_EQ(seen[2].second, 5);
  CHECK_EQ(seen[3].first - seen[2].first, LSF_BTN_STEP_MS);       // normal pace in between
  CHECK_EQ(seen[3].second, 8);
}

static void testHoldDownPausesAtTheBottom() {
  LsfButton b;
  uint8_t bri = 13;                                    // 5 %
  std::vector<std::pair<uint32_t, unsigned>> seen;
  for (uint32_t t = 0; t <= 6000; t++) {
    // press, release, press again and hold
    const bool pressed = (t >= 100 && t < 160) || (t >= 300 && t < 5000);
    LsfBtnEvent e = b.poll(pressed, 1000 + t);
    if (e == LSFB_HOLD_DOWN) {
      const uint8_t v = lsfPrevBrightness(bri, 5);
      if (v == bri) continue;                          // already at the floor: no change, nothing to pause
      bri = v;
      seen.push_back({t, bri});
      if (lsfAtBrightnessEnd(bri)) b.delayNextStep(1000 + t, LSF_BTN_END_HOLD_MS);
    }
  }
  CHECK_EQ(seen.size(), 4u);                           // 5 % -> 4, 3, 2, 1 %, then it stays
  CHECK_EQ(seen[0].second, 10); CHECK_EQ(seen[1].second, 8); CHECK_EQ(seen[2].second, 5); CHECK_EQ(seen[3].second, 3);
  CHECK_EQ(seen[1].first - seen[0].first, LSF_BTN_STEP_MS);
}

static void testDelayAcrossClockWrap() {
  LsfButton b;
  const uint32_t t0 = 0xFFFFFFFFu - 700;               // the clock wraps during the 1.5 s pause
  int steps = 0; uint32_t first = 0, second = 0;
  for (uint32_t t = 0; t <= 4000; t++) {
    LsfBtnEvent e = b.poll(t >= 100 && t < 3900, t0 + t);
    if (e == LSFB_HOLD_UP) {
      steps++;
      if (steps == 1) { first = t; b.delayNextStep(t0 + t, LSF_BTN_END_HOLD_MS); }
      if (steps == 2) second = t;
    }
  }
  CHECK_EQ(second - first, LSF_BTN_END_HOLD_MS);
}

static void testIdSet() {
  LsfIdSet s;
  CHECK_EQ(s.count(), 0u);
  CHECK_EQ(s.next(0), 0u);                             // empty set
  s.add(3); s.add(1); s.add(250); s.add(7);
  s.add(0); s.add(251); s.add(255); s.add(1000);       // out of range: ignored
  CHECK_EQ(s.count(), 4u);
  CHECK_EQ(s.next(0), 1u);
  CHECK_EQ(s.next(1), 3u);
  CHECK_EQ(s.next(3), 7u);
  CHECK_EQ(s.next(7), 250u);
  CHECK_EQ(s.next(250), 1u);                           // wraps
  CHECK_EQ(s.next(255), 1u);                           // a temporary preset (255) is not in the list: start over
  CHECK_EQ(s.next(4), 7u);                             // current preset need not be in the set
  s.clear();
  CHECK_EQ(s.count(), 0u);
  s.add(2);
  CHECK_EQ(s.next(2), 2u);                             // a single preset stays itself
}

int main() {
  testClick(); testDouble(); testTwoSlowClicksAreTwoClicks(); testTripleIsDoublePlusClick();
  testHoldStepsThenStopsOnRelease(); testShortPressJustUnderHoldIsClick(); testDoubleClickAndHoldDims();
  testSingleHoldAfterAnIdleGapStillBrightens(); testDimThenUpDirectionsDoNotLeak();
  testBounceIsIgnored(); testHeldAtBootIsIgnoredUntilReleased(); testClockWrap(); testSlowPolling();
  testBrightnessSteps(); testBrightnessDown(); testBrightnessEnds(); testHoldPausesAtTheEnds();
  testHoldDownPausesAtTheBottom(); testDelayAcrossClockWrap(); testIdSet();
  if (failures) { std::printf("%d failure(s)\n", failures); return 1; }
  std::printf("button tests: ok\n");
  return 0;
}
