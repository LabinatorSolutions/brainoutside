/* ============================================================
   Shared vocabulary for the brain visuals (M3.5).

   The rings, the explorer and everything after them draw the same
   entities, so they must agree on what a `take` looks like and how big
   a well-read note is. That agreement lives here, once.

   Load before rings.js / explorer.js (both are `defer`, so document
   order is execution order).
   ============================================================ */
(function (global) {
  "use strict";

  var KIND_COLOR = {
    identity: "#1a1a2e",
    project: "#e8a07a",
    take: "#6366f1",
    story: "#9b7cc4",
    lesson: "#4db8a8",
    fact: "#3f8fbf",
    lens: "#d99b00",
    catalog: "#8a8a99"
  };

  // Display order: what the brain is (identity, projects) before what it
  // knows (notes), reference material last. Also the ring sort order, so
  // each tier reads as arcs of kind instead of confetti.
  var KIND_ORDER = ["identity", "project", "take", "story", "lesson", "fact", "lens", "catalog"];

  var FALLBACK_COLOR = "#8a8a99";
  var DOT_MIN = 3.6;
  var DOT_MAX = 11;

  global.BrainViz = {
    KIND_COLOR: KIND_COLOR,
    KIND_ORDER: KIND_ORDER,
    TOPIC_COLOR: "#b9b9c6",

    color: function (kind) {
      return KIND_COLOR[kind] || FALLBACK_COLOR;
    },

    kindRank: function (kind) {
      var i = KIND_ORDER.indexOf(kind);
      return i === -1 ? KIND_ORDER.length : i;
    },

    /* Radius from read count. sqrt, not linear: one note served 40 times
       shouldn't swallow its neighbours, but the difference must stay
       visible at a glance. */
    dotRadius: function (reads) {
      return Math.min(DOT_MAX, DOT_MIN + 2.4 * Math.sqrt(reads || 0));
    }
  };
})(window);
