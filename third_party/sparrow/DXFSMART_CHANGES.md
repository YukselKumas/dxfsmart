# DXFsmart changes to sparrow / jagua-rs

Upstream: sparrow https://github.com/JeroenGar/sparrow (MIT, commit 1fe3e31),
jagua-rs 0.7 https://github.com/JeroenGar/jagua-rs (MPL-2.0, vendored in `vendor/jagua-rs`).

DXFsmart needs *sheets*, not one endless strip. The changes add optional sheet
barriers to the strip packing problem so a single optimisation minimises the
number of sheets and the length used on the last one:

* `vendor/jagua-rs/src/probs/spp/entities/strip.rs`, `io/ext_repr.rs`, `io/import.rs`:
  optional `sheet_period`, `barrier_offset`, `barrier_width` in the instance JSON.
  Thin forbidden bands (quality-0 zones) are placed every `sheet_period` along the strip.
* `src/quantify/mod.rs`, `src/quantify/tracker.rs`, `src/eval/collision_loss.rs`,
  `src/util/assertions.rs`: overlap loss for items colliding with such a band
  (shortest horizontal escape distance x item height, like the container loss).
* `src/util/io.rs`: `--shrink-step` CLI option for the exploration phase.

All modified source files are published here, as required by MPL-2.0.
