/* ============================================================
   M3.5.5 — belief timeline.

   Two panels over one shared month axis, both drawn from graph.json:

   1. Brain growth — how many notes the brain gained each month, stacked
      by kind, with the running total behind it.
   2. Belief chains — every `superseded_by` chain as position-over-time:
      the old note, hollow, pointing at what replaced it. The brain
      supersedes, never deletes (CLAUDE.md §5.3), so this is the honest
      record of the operator changing their mind.

   Plain SVG, like the rings: bars, a line and some arrows don't justify
   a charting dependency.
   ============================================================ */
(function () {
  "use strict";

  var VIZ = window.BrainViz;
  var SVG_NS = "http://www.w3.org/2000/svg";

  var W = 900;
  var PAD = { left: 34, right: 40, top: 14, bottom: 26 };

  function svg(name, attrs) {
    var node = document.createElementNS(SVG_NS, name);
    for (var k in attrs) {
      if (attrs[k] !== null && attrs[k] !== undefined) node.setAttribute(k, attrs[k]);
    }
    return node;
  }

  function text(x, y, str, cls) {
    var t = svg("text", { x: x, y: y, "class": cls || "tl-label" });
    t.textContent = str;
    return t;
  }

  /* Inclusive month range, gaps filled: a month with no notes is part of
     the story, so it gets a slot rather than being skipped. */
  function monthSpan(months) {
    if (!months.length) return [];
    var sorted = months.slice().sort();
    var out = [];
    var cur = sorted[0];
    var last = sorted[sorted.length - 1];
    var guard = 0;
    while (cur <= last && guard++ < 600) {
      out.push(cur);
      var y = parseInt(cur.slice(0, 4), 10);
      var m = parseInt(cur.slice(5, 7), 10) + 1;
      if (m > 12) {
        m = 1;
        y += 1;
      }
      cur = y + "-" + (m < 10 ? "0" + m : m);
    }
    return out;
  }

  function growth(host, entities, axis) {
    var H = 210;
    var plotW = W - PAD.left - PAD.right;
    var plotH = H - PAD.top - PAD.bottom;
    var slot = plotW / Math.max(1, axis.length);

    var perMonth = {};
    var kinds = {};
    entities.forEach(function (e) {
      if (!e.date) return;
      perMonth[e.date] = perMonth[e.date] || {};
      perMonth[e.date][e.kind] = (perMonth[e.date][e.kind] || 0) + 1;
      kinds[e.kind] = true;
    });
    var kindList = Object.keys(kinds).sort(function (a, b) {
      return VIZ.kindRank(a) - VIZ.kindRank(b);
    });

    var peak = 1;
    var total = 0;
    axis.forEach(function (m) {
      var n = 0;
      kindList.forEach(function (k) {
        n += (perMonth[m] || {})[k] || 0;
      });
      peak = Math.max(peak, n);
      total += n;
    });

    var fig = svg("svg", {
      "class": "tl-figure",
      viewBox: "0 0 " + W + " " + H,
      role: "img",
      "aria-label": "Notes added per month, stacked by kind"
    });

    // Running total, behind the bars — the brain's size over time.
    var running = 0;
    var points = axis.map(function (m, i) {
      kindList.forEach(function (k) {
        running += (perMonth[m] || {})[k] || 0;
      });
      var x = PAD.left + i * slot + slot / 2;
      var y = PAD.top + plotH - (running / Math.max(1, total)) * plotH;
      return x.toFixed(1) + "," + y.toFixed(1);
    });
    fig.appendChild(svg("polyline", { "class": "tl-cumulative", points: points.join(" ") }));
    fig.appendChild(text(W - PAD.right + 6, PAD.top + 4, String(total), "tl-axis"));
    fig.appendChild(text(W - PAD.right + 6, PAD.top + 16, "total", "tl-axis"));

    fig.appendChild(
      svg("line", {
        "class": "tl-baseline",
        x1: PAD.left,
        y1: PAD.top + plotH,
        x2: W - PAD.right,
        y2: PAD.top + plotH
      })
    );
    fig.appendChild(text(PAD.left - 6, PAD.top + 8, String(peak), "tl-axis tl-axis-end"));

    axis.forEach(function (m, i) {
      var x = PAD.left + i * slot;
      var bw = Math.min(46, slot * 0.62);
      var bx = x + (slot - bw) / 2;
      var y = PAD.top + plotH;
      var monthTotal = 0;

      kindList.forEach(function (kind) {
        var n = (perMonth[m] || {})[kind] || 0;
        if (!n) return;
        monthTotal += n;
        var h = (n / peak) * plotH;
        y -= h;
        var bar = svg("rect", {
          "class": "tl-bar",
          x: bx.toFixed(1),
          y: y.toFixed(1),
          width: bw.toFixed(1),
          height: h.toFixed(1),
          fill: VIZ.color(kind)
        });
        var title = svg("title");
        title.textContent = m + " · " + kind + " ×" + n;
        bar.appendChild(title);
        fig.appendChild(bar);
      });

      if (monthTotal) {
        fig.appendChild(text(bx + bw / 2, y - 5, String(monthTotal), "tl-value"));
      }
      fig.appendChild(text(x + slot / 2, PAD.top + plotH + 16, m.slice(2), "tl-month"));
    });

    host.appendChild(fig);
    return kindList;
  }

  /* Follow `superseded_by` to the end: a chain can be longer than two
     notes, and only the last note is a current position. */
  function chainsOf(nodes, edges) {
    var byId = {};
    nodes.forEach(function (n) {
      if (n.type === "entity") byId[n.id] = n;
    });
    var next = {};
    var hasParent = {};
    edges.forEach(function (e) {
      if (e.type !== "superseded") return;
      next[e.source] = e.target;
      hasParent[e.target] = true;
    });

    return Object.keys(next)
      .filter(function (id) {
        return !hasParent[id]; // start only from the oldest link
      })
      .map(function (id) {
        var chain = [];
        var cur = id;
        var guard = 0;
        while (cur && byId[cur] && guard++ < 50) {
          chain.push(byId[cur]);
          cur = next[cur];
        }
        return chain;
      })
      .filter(function (c) {
        return c.length > 1;
      });
  }

  function beliefs(host, chains, axis) {
    if (!chains.length) {
      var empty = document.createElement("p");
      empty.className = "tl-empty";
      empty.textContent =
        "No position has been superseded yet — every note in the brain is current. " +
        "When a new claim replaces an old one the old note stays, marked superseded, " +
        "and the chain shows up here as a line across time.";
      host.appendChild(empty);
      return;
    }

    var rowH = 46;
    var H = PAD.top + chains.length * rowH + PAD.bottom;
    var plotW = W - PAD.left - PAD.right;
    var slot = plotW / Math.max(1, axis.length);

    var fig = svg("svg", {
      "class": "tl-figure",
      viewBox: "0 0 " + W + " " + H,
      role: "img",
      "aria-label": chains.length + " superseded belief chains over time"
    });

    var defs = svg("defs");
    var marker = svg("marker", {
      id: "tl-arrow",
      viewBox: "0 0 8 8",
      refX: 7,
      refY: 4,
      markerWidth: 6,
      markerHeight: 6,
      orient: "auto"
    });
    marker.appendChild(svg("path", { d: "M0,0 L8,4 L0,8 z", "class": "tl-arrow-head" }));
    defs.appendChild(marker);
    fig.appendChild(defs);

    function xOf(date) {
      var i = axis.indexOf(date);
      if (i === -1) i = date && date > axis[axis.length - 1] ? axis.length - 1 : 0;
      return PAD.left + i * slot + slot / 2;
    }

    chains.forEach(function (chain, row) {
      var y = PAD.top + row * rowH + rowH / 2;
      fig.appendChild(
        svg("line", { "class": "tl-lane", x1: PAD.left, y1: y, x2: W - PAD.right, y2: y })
      );

      chain.forEach(function (node, i) {
        var x = xOf(node.date);
        if (i > 0) {
          var prev = xOf(chain[i - 1].date);
          fig.appendChild(
            svg("line", {
              "class": "tl-link",
              x1: prev + 8,
              y1: y,
              x2: Math.max(prev + 18, x - 8),
              y2: y,
              "marker-end": "url(#tl-arrow)"
            })
          );
        }
        var dot = svg("circle", {
          "class": "tl-dot",
          cx: x,
          cy: y,
          r: 6,
          fill: node.current ? VIZ.color(node.kind) : "none",
          stroke: VIZ.color(node.kind),
          "stroke-width": node.current ? 0 : 1.6
        });
        var title = svg("title");
        title.textContent =
          node.id + " (" + node.date + (node.current ? ", current" : ", superseded") + ")";
        dot.appendChild(title);
        dot.addEventListener("click", function () {
          if (node.url) window.location.href = node.url;
        });
        fig.appendChild(dot);
      });

      var head = chain[chain.length - 1];
      fig.appendChild(text(PAD.left, y - 14, head.title || head.id, "tl-chain-label"));
    });

    axis.forEach(function (m, i) {
      fig.appendChild(
        text(PAD.left + i * slot + slot / 2, H - 8, m.slice(2), "tl-month")
      );
    });

    host.appendChild(fig);
  }

  function mount(root) {
    var url = root.getAttribute("data-graph-url");
    var growthHost = root.querySelector("[data-tl-growth]");
    var beliefHost = root.querySelector("[data-tl-beliefs]");
    var growthNote = root.querySelector("[data-tl-growth-note]");
    var beliefNote = root.querySelector("[data-tl-belief-note]");
    var legendHost = root.querySelector("[data-tl-legend]");

    fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (r) {
        if (!r.ok) throw new Error("graph.json returned " + r.status);
        return r.json();
      })
      .then(function (data) {
        var entities = data.nodes.filter(function (n) {
          return n.type === "entity";
        });
        var dated = entities.filter(function (e) {
          return e.date;
        });
        var chains = chainsOf(data.nodes, data.edges);

        var months = dated.map(function (e) {
          return e.date;
        });
        chains.forEach(function (c) {
          c.forEach(function (n) {
            if (n.date) months.push(n.date);
          });
        });
        var axis = monthSpan(months);

        if (!axis.length) {
          growthHost.innerHTML = "";
          growthNote.textContent = "No dated notes yet.";
          beliefs(beliefHost, [], axis);
          return;
        }

        var kinds = growth(growthHost, dated, axis);
        growthNote.textContent =
          dated.length + " dated notes across " + axis.length + " months (" +
          axis[0] + " → " + axis[axis.length - 1] + ") · " +
          (entities.length - dated.length) +
          " entities carry no date (project cards, identity, catalogs, lenses).";

        beliefs(beliefHost, chains, axis);
        beliefNote.textContent = chains.length
          ? chains.length + (chains.length === 1 ? " chain" : " chains") + " · " +
            chains.reduce(function (n, c) {
              return n + c.length;
            }, 0) + " notes · hollow = superseded, arrow points at what replaced it"
          : "";

        kinds.forEach(function (kind) {
          var item = document.createElement("span");
          item.className = "tl-key-item";
          var swatch = document.createElement("span");
          swatch.className = "tl-swatch";
          swatch.style.background = VIZ.color(kind);
          item.appendChild(swatch);
          item.appendChild(document.createTextNode(kind));
          legendHost.appendChild(item);
        });
      })
      .catch(function (err) {
        growthNote.textContent = "Could not draw the timeline: " + err.message;
      });
  }

  document.addEventListener("DOMContentLoaded", function () {
    Array.prototype.forEach.call(document.querySelectorAll("[data-timeline]"), mount);
  });
})();
