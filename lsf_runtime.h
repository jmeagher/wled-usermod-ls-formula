// Run-time support for the WLED side of LS Formula: the pure logic, with no WLED or Arduino dependency, so
// tests/test_lsf.cpp can run it on the desktop. ls_formula.cpp wires it to WLED.
//
//   LsfProgramStore  lets the web task replace or remove a slot's program while the render task may be in the
//                    middle of running it (double buffering with one spare program, published atomically).
//   LsfFrameGuard    a time budget for all formula segments of one strip frame, and the per-slot status that the
//                    state JSON reports, latched once per frame so it does not change from segment to segment.
//   lsfSlowFrame()   the per-segment slow-frame counter that switches a formula off.
#pragma once
#include <atomic>
#include <stddef.h>
#include <stdint.h>
#include "lsf.h"

// The render task and the web task share the std::atomic members below. On the ESP32-C6 (RISC-V with the A
// extension) they compile to native atomic instructions; on the original ESP32 (Xtensa) the toolchain does not report
// them as always lock-free and ESP-IDF implements them with a short critical section, which is equally correct here
// (nothing waits on them for long), so there is deliberately no is_always_lock_free assertion.

// ------------------------------------------------------------------------------------------ programs ------

// A compiled program plus its run-time status. The status is written by the renderer, so it lives with the program
// it describes: a slow frame of an old program can never switch off the new one that replaced it.
struct LsfRun {
  LsfProgram            prog;
  uint16_t              gen = 0;          // install generation: lets a segment tell that its program was replaced
  std::atomic<bool>     disabled{false};  // switched off by the slow-frame guard
  std::atomic<uint32_t> worstUs{0};
  std::atomic<uint32_t> avgUs{0};         // exponential moving average of the real frame time
  char                  err[40] = "";     // why it was switched off; written before `disabled` is set

  void resetStatus(uint16_t generation) {
    gen = generation;
    disabled.store(false);
    worstUs.store(0, std::memory_order_relaxed);
    avgUs.store(0, std::memory_order_relaxed);
    err[0] = 0;
  }
};

// SLOTS installed programs plus one spare. Exactly one renderer (WLED draws all segments from one task) and one
// writer at a time (the caller serializes writers; in WLED all of them hold the JSON buffer lock).
//
// The renderer brackets each use with a Reader, which bumps `seq_` on entry and on exit, so `seq_` is odd while a
// program may be in use. The writer fills a buffer that no slot owns, then swaps it in with one atomic exchange. The
// buffer it takes out is not reused until the renderer has left the Reader it was in at the time of the swap: if
// `seq_` was even then, no reader was inside; if it was odd, the reader inside may hold the old pointer, and any
// change of `seq_` means that reader has left (a later reader loads the new pointer). seq_cst operations give the
// single total order this argument needs.
template <unsigned SLOTS>
class LsfProgramStore {
 public:
  static const unsigned kBuffers = SLOTS + 1;

  LsfProgramStore() {
    for (unsigned k = 0; k < SLOTS; k++) cur_[k].store(nullptr);
    for (unsigned k = 0; k < kBuffers; k++) { owned_[k] = false; retired_[k] = false; retireSeq_[k] = 0; }
  }

  // --- writer side ---

  // A buffer that no slot owns and that the renderer cannot still be reading, or nullptr when every such buffer is
  // still in use by the frame being drawn (try again shortly).
  LsfRun* acquire() {
    for (unsigned k = 0; k < kBuffers; k++) {
      if (owned_[k]) continue;
      if (retired_[k]) {
        const uint32_t r = retireSeq_[k];
        if ((r & 1u) != 0 && seq_.load() == r) continue;   // the reader that may hold it has not left yet
        retired_[k] = false;
      }
      return &bufs_[k];
    }
    return nullptr;
  }

  // Makes `b` (from acquire(), fully written) the program of `slot`, or empties the slot when `b` is nullptr.
  void publish(unsigned slot, LsfRun* b) {
    if (slot >= SLOTS) return;
    if (b) owned_[index(b)] = true;
    LsfRun* old = cur_[slot].exchange(b);
    if (old) {
      const unsigned i = index(old);
      owned_[i] = false;
      retired_[i] = true;
      retireSeq_[i] = seq_.load();
    }
  }

  // The installed program of `slot` (nullptr when empty). For the writer side and for status reports, which run
  // under the same lock as the writer, so the buffer cannot be recycled while it is being read.
  const LsfRun* installed(unsigned slot) const { return slot < SLOTS ? cur_[slot].load() : nullptr; }

  // --- reader side (the renderer) ---

  class Reader {
   public:
    explicit Reader(LsfProgramStore& s) : s_(s) { s_.seq_.fetch_add(1); }
    ~Reader() { s_.seq_.fetch_add(1); }
    // The program to draw with; stays valid until this Reader is destroyed. Load it once per frame of a segment.
    LsfRun* get(unsigned slot) const { return slot < SLOTS ? s_.cur_[slot].load() : nullptr; }
    Reader(const Reader&) = delete;
    Reader& operator=(const Reader&) = delete;
   private:
    LsfProgramStore& s_;
  };

  // For tests.
  uint32_t seq() const { return seq_.load(); }

 private:
  unsigned index(const LsfRun* b) const { return (unsigned)(b - bufs_); }

  LsfRun                bufs_[kBuffers];
  std::atomic<LsfRun*>  cur_[SLOTS];
  std::atomic<uint32_t> seq_{0};
  // Writer-only bookkeeping.
  bool                  owned_[kBuffers];
  bool                  retired_[kBuffers];
  uint32_t              retireSeq_[kBuffers];
};

// ----------------------------------------------------------------------------------- slow-frame guard ------

// Per-segment counter of consecutive slow frames (WLED keeps it in the segment's aux0/aux1). `segGen` remembers which
// install the count belongs to, so a replaced formula starts from zero. Returns true when the formula has been too
// slow `needed` frames in a row on this segment and must be switched off.
inline bool lsfSlowFrame(uint16_t& count, uint16_t& segGen, uint16_t runGen, uint32_t us, uint32_t limitUs,
                         uint16_t needed = 3) {
  if (segGen != runGen) { segGen = runGen; count = 0; }
  if (us <= limitUs) { count = 0; return false; }
  if (count < 0xFFFFu) count++;
  return count >= needed;
}

// ------------------------------------------------------------------------------------ frame budget ------

// One strip frame can draw many segments, each running a formula, and a single formula is only checked against its
// own segment. This guard adds up the estimated time of all formula segments of the frame and blanks the rest once
// `budgetUs` would be exceeded, and it stops drawing formulas for the rest of the frame once the real time spent
// passes `hardUs` (the estimate may be low). It also collects the per-slot status of the frame, which becomes
// visible (report()) when the next frame starts, so a status never mixes two frames or two segments.
//
// Written only by the renderer; report() may be called from another task (relaxed atomics: a report can mix two
// consecutive frames' counters, which is harmless for status display).
template <unsigned SLOTS>
class LsfFrameGuard {
 public:
  enum Verdict { DRAW, OVER_BUDGET, SKIP };

  struct SlotStatus {
    uint16_t segs = 0;       // segments drawn with this slot
    uint16_t over = 0;       // segments blanked because the formula alone is over its budget for that segment
    uint16_t skipped = 0;    // segments blanked because the frame budget was used up
    uint32_t overEstUs = 0;  // largest over-budget estimate, and the size of that segment
    uint32_t overPixels = 0;
  };
  struct FrameStatus {
    uint32_t estUs = 0, realUs = 0;
    uint16_t segs = 0, skipped = 0;
  };

  LsfFrameGuard(uint32_t segBudgetUs, uint32_t frameBudgetUs, uint32_t hardUs)
      : segBudget_(segBudgetUs), frameBudget_(frameBudgetUs), hard_(hardUs) {}

  // Before drawing a segment with slot `slot`. `frameId` changes once per strip frame (WLED: strip.now), `nowMs` is
  // a millisecond clock for staleness, `estUs` the formula's estimate for this segment of `pixels` pixels.
  Verdict admit(unsigned slot, uint32_t frameId, uint32_t nowMs, uint32_t estUs, uint32_t pixels) {
    if (!started_ || frameId != frameId_) startFrame(frameId, nowMs);
    if (slot >= SLOTS) return SKIP;
    SlotStatus& s = slots_[slot];
    if (estUs > segBudget_) {
      if (s.over < 0xFFFFu) s.over++;
      if (estUs > s.overEstUs) { s.overEstUs = estUs; s.overPixels = pixels; }
      return OVER_BUDGET;
    }
    if (frame_.realUs >= hard_ || estUs > frameBudget_ - frame_.estUs) {   // frame_.estUs <= frameBudget_ always
      if (s.skipped < 0xFFFFu) s.skipped++;
      if (frame_.skipped < 0xFFFFu) frame_.skipped++;
      return SKIP;
    }
    frame_.estUs += estUs;
    if (s.segs < 0xFFFFu) s.segs++;
    if (frame_.segs < 0xFFFFu) frame_.segs++;
    return DRAW;
  }

  // After drawing a segment that admit() let through: the real time it took.
  void charge(uint32_t realUs) {
    const uint32_t t = frame_.realUs + realUs;
    frame_.realUs = t < frame_.realUs ? 0xFFFFFFFFu : t;
  }

  // Status of the last complete frame. `recent` is false when no formula segment has been drawn in the last
  // `staleMs` (the effect is no longer in use, or the strip is off): the counters are then all zero.
  bool report(uint32_t nowMs, uint32_t staleMs, FrameStatus* frame, SlotStatus* slots) const {
    const bool recent = pubValid_.load(std::memory_order_relaxed) &&
                        (uint32_t)(nowMs - pubMs_.load(std::memory_order_relaxed)) <= staleMs;
    if (frame) *frame = FrameStatus();
    if (slots) for (unsigned k = 0; k < SLOTS; k++) slots[k] = SlotStatus();
    if (!recent) return false;
    if (frame) {
      frame->estUs = pubEst_.load(std::memory_order_relaxed);
      frame->realUs = pubReal_.load(std::memory_order_relaxed);
      frame->segs = (uint16_t)pubSegs_.load(std::memory_order_relaxed);
      frame->skipped = (uint16_t)pubSkipped_.load(std::memory_order_relaxed);
    }
    if (slots) {
      for (unsigned k = 0; k < SLOTS; k++) {
        slots[k].segs = (uint16_t)pubSlot_[k].segs.load(std::memory_order_relaxed);
        slots[k].over = (uint16_t)pubSlot_[k].over.load(std::memory_order_relaxed);
        slots[k].skipped = (uint16_t)pubSlot_[k].skipped.load(std::memory_order_relaxed);
        slots[k].overEstUs = pubSlot_[k].overEstUs.load(std::memory_order_relaxed);
        slots[k].overPixels = pubSlot_[k].overPixels.load(std::memory_order_relaxed);
      }
    }
    return true;
  }

 private:
  void startFrame(uint32_t frameId, uint32_t nowMs) {
    if (started_) {   // publish the frame that just ended
      pubEst_.store(frame_.estUs, std::memory_order_relaxed);
      pubReal_.store(frame_.realUs, std::memory_order_relaxed);
      pubSegs_.store(frame_.segs, std::memory_order_relaxed);
      pubSkipped_.store(frame_.skipped, std::memory_order_relaxed);
      for (unsigned k = 0; k < SLOTS; k++) {
        pubSlot_[k].segs.store(slots_[k].segs, std::memory_order_relaxed);
        pubSlot_[k].over.store(slots_[k].over, std::memory_order_relaxed);
        pubSlot_[k].skipped.store(slots_[k].skipped, std::memory_order_relaxed);
        pubSlot_[k].overEstUs.store(slots_[k].overEstUs, std::memory_order_relaxed);
        pubSlot_[k].overPixels.store(slots_[k].overPixels, std::memory_order_relaxed);
      }
      pubMs_.store(frameMs_, std::memory_order_relaxed);
      pubValid_.store(true, std::memory_order_relaxed);
    }
    started_ = true;
    frameId_ = frameId;
    frameMs_ = nowMs;
    frame_ = FrameStatus();
    for (unsigned k = 0; k < SLOTS; k++) slots_[k] = SlotStatus();
  }

  const uint32_t segBudget_, frameBudget_, hard_;
  // The frame being drawn (renderer only).
  bool        started_ = false;
  uint32_t    frameId_ = 0, frameMs_ = 0;
  FrameStatus frame_;
  SlotStatus  slots_[SLOTS];
  // The last complete frame (read by report()).
  struct PubSlot { std::atomic<uint32_t> segs{0}, over{0}, skipped{0}, overEstUs{0}, overPixels{0}; };
  std::atomic<bool>     pubValid_{false};
  std::atomic<uint32_t> pubMs_{0}, pubEst_{0}, pubReal_{0}, pubSegs_{0}, pubSkipped_{0};
  PubSlot               pubSlot_[SLOTS];
};
