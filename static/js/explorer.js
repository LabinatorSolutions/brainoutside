/* ============================================================
   M3.5.3 — graph explorer.

   The whole brain as a force layout: entity nodes plus topic hubs, so
   notes physically cluster around what they are about. Selecting a lens
   highlights exactly the subgraph that lens scopes — the membership is
   computed server-side (graph.py `lens_definitions`) so the picture and
   any future reader-side lens filter are the same computation, not two
   implementations that drift.

   Cytoscape is vendored (static/vendor/, MIT) — no CDN, ever.
   ============================================================ */
(function () {
  "use strict";

  var VIZ = window.BrainViz;

  var EDGE_STYLE = {
    topic: { color: "#c9c9d4", width: 1, dashed: false },
    project: { color: "#e8a07a", width: 1.6, dashed: false },
    source: { color: "#9b7cc4", width: 1.2, dashed: true },
    superseded: { color: "#c0392b", width: 1.6, dashed: false }
  };

  function nodeElements(data) {
    return data.nodes.map(function (n) {
      if (n.type === "topic") {
        return {
          data: {
            id: n.id,
            type: "topic",
            label: n.label,
            size: Math.min(46, 16 + n.size * 1.5),
            color: VIZ.TOPIC_COLOR
          }
        };
      }
      return {
        data: {
          id: n.id,
          type: "entity",
          label: n.title || n.id,
          kind: n.kind,
          tier: n.tier,
          reads: n.reads,
          topics: (n.topics || []).join(", "),
          current: n.current,
          url: n.url,
          size: VIZ.dotRadius(n.reads) * 2.2,
          color: VIZ.color(n.kind),
          // Superseded notes are history, never a current position
          // (CLAUDE.md §6.6) — hollow here as on the rings.
          fill: n.current ? VIZ.color(n.kind) : "#fffef7",
          border: n.current ? 0 : 1.6
        }
      };
    });
  }

  function edgeElements(data) {
    return data.edges.map(function (e, i) {
      var style = EDGE_STYLE[e.type] || EDGE_STYLE.topic;
      return {
        data: {
          id: "e" + i,
          source: e.source,
          target: e.target,
          type: e.type,
          color: style.color,
          width: style.width,
          dash: style.dashed ? "dashed" : "solid"
        }
      };
    });
  }

  var STYLE = [
    {
      selector: 'node[type="entity"]',
      style: {
        "background-color": "data(fill)",
        "border-width": "data(border)",
        "border-color": "data(color)",
        width: "data(size)",
        height: "data(size)",
        label: "",
        "transition-property": "opacity",
        "transition-duration": "120ms"
      }
    },
    {
      selector: 'node[type="topic"]',
      style: {
        "background-color": "#ffffff",
        "background-opacity": 0.85,
        "border-width": 1,
        "border-color": "data(color)",
        shape: "round-rectangle",
        width: "data(size)",
        height: 18,
        label: "data(label)",
        "font-size": 10,
        "font-family": "JetBrains Mono, monospace",
        color: "#6b6b7b",
        "text-valign": "center",
        "text-halign": "center"
      }
    },
    {
      selector: "edge",
      style: {
        "line-color": "data(color)",
        width: "data(width)",
        "line-style": "data(dash)",
        "curve-style": "haystack",
        opacity: 0.5
      }
    },
    { selector: 'edge[type="superseded"]', style: { "curve-style": "bezier", "target-arrow-shape": "triangle", "target-arrow-color": "data(color)" } },
    {
      selector: "node.loose",
      style: {
        label: "data(label)",
        "font-size": 9,
        "font-family": "JetBrains Mono, monospace",
        color: "#6b6b7b",
        "text-halign": "right",
        "text-valign": "center",
        "text-margin-x": 7
      }
    },
    { selector: ".dim", style: { opacity: 0.07 } },
    { selector: ".pick", style: { "border-width": 3, "border-color": "#4f46e5", opacity: 1 } },
    // After .dim on purpose: a note being served right now is worth
    // seeing even when the current lens has it dimmed out.
    {
      selector: ".pulse",
      style: {
        opacity: 1,
        "border-width": 10,
        "border-color": "#4f46e5",
        "border-opacity": 0.45
      }
    }
  ];

  function mount(root) {
    var url = root.getAttribute("data-graph-url");
    var canvas = root.querySelector("[data-graph-canvas]");
    var picker = root.querySelector("[data-lens-picker]");
    var scope = root.querySelector("[data-lens-scope]");
    var tip = root.querySelector("[data-graph-tip]");
    var status = root.querySelector("[data-graph-status]");
    var resetBtn = root.querySelector("[data-graph-reset]");
    var legend = root.querySelector("[data-graph-legend]");
    var ticker = root.querySelector("[data-graph-ticker]");

    fetch(url, { credentials: "same-origin", headers: { Accept: "application/json" } })
      .then(function (r) {
        if (!r.ok) throw new Error("graph.json returned " + r.status);
        return r.json();
      })
      .then(function (data) {
        draw(data);
      })
      .catch(function (err) {
        status.textContent = "Could not load the graph: " + err.message;
      });

    function draw(data) {
      status.textContent =
        data.counts.entities + " entities · " + data.counts.topics + " topics · " +
        data.counts.edges.total + " links";

      var cy = cytoscape({
        container: canvas,
        elements: { nodes: nodeElements(data), edges: edgeElements(data) },
        style: STYLE,
        wheelSensitivity: 0.2,
        // Entities with no topics (catalogs, identity files) are their own
        // components; left to spread they push the connected core into a
        // corner, so componentSpacing stays tight and gravity high.
        layout: {
          name: "cose",
          animate: false,
          randomize: true,
          idealEdgeLength: 45,
          nodeRepulsion: 5200,
          edgeElasticity: 55,
          gravity: 90,
          numIter: 1500,
          componentSpacing: 32,
          nodeDimensionsIncludeLabels: true,
          fit: true,
          padding: 20
        }
      });

      parkUnlinked(cy);
      buildLegend(legend, data);
      buildPicker(picker, data);

      var lensById = {};
      data.lenses.forEach(function (l) {
        lensById[l.id] = l;
      });

      function clearHighlight() {
        cy.elements().removeClass("dim").removeClass("pick");
      }

      /* Highlight = exactly the ids the server resolved for this lens,
         plus the lens node and the topic hubs it names. Nothing is
         recomputed here: if the picture and the definition ever
         disagree, that's a server bug, not a drawing bug. */
      function applyLens(id) {
        clearHighlight();
        var lens = lensById[id];
        if (!lens) {
          scope.textContent = "";
          return null;
        }
        var keep = {};
        lens.members.forEach(function (m) {
          keep[m] = true;
        });
        keep[lens.id] = true;
        lens.topics.forEach(function (t) {
          keep["topic:" + t] = true;
        });

        cy.batch(function () {
          cy.nodes().forEach(function (n) {
            if (!keep[n.id()]) n.addClass("dim");
          });
          cy.edges().forEach(function (e) {
            if (!keep[e.source().id()] || !keep[e.target().id()]) e.addClass("dim");
          });
          cy.getElementById(lens.id).addClass("pick");
        });

        scope.textContent =
          lens.member_count + " of " + data.counts.entities + " entities in scope · " +
          "ceiling " + lens.ceiling + " · types " + lens.types.join("/");
        return lens;
      }

      picker.addEventListener("change", function () {
        applyLens(picker.value);
      });
      resetBtn.addEventListener("click", function () {
        picker.value = "";
        clearHighlight();
        scope.textContent = "";
        cy.fit(undefined, 24);
      });

      cy.on("tap", 'node[type="entity"]', function (evt) {
        var url = evt.target.data("url");
        if (url) window.location.href = url;
      });

      // Tapping a topic hub isolates its neighbourhood — the cheapest
      // way to answer "what does the brain know about X?".
      cy.on("tap", 'node[type="topic"]', function (evt) {
        clearHighlight();
        picker.value = "";
        var hood = evt.target.closedNeighborhood();
        cy.elements().difference(hood).addClass("dim");
        evt.target.addClass("pick");
        scope.textContent =
          evt.target.data("label") + " · " + (hood.nodes().length - 1) + " entities";
      });

      cy.on("mouseover", "node", function (evt) {
        var n = evt.target;
        showTip(n);
      });
      cy.on("mouseout", "node", function () {
        tip.classList.remove("is-on");
      });
      cy.on("pan zoom", function () {
        tip.classList.remove("is-on");
      });

      function showTip(n) {
        tip.textContent = "";
        var title = document.createElement("div");
        title.className = "gx-tip-title";
        title.textContent = n.data("label");
        tip.appendChild(title);

        var meta = document.createElement("div");
        meta.className = "gx-tip-meta";
        if (n.data("type") === "topic") {
          meta.textContent = "topic · " + n.degree() + " links";
        } else {
          var bits = [n.data("kind"), n.data("tier"), n.data("reads") + " reads"];
          if (!n.data("current")) bits.push("superseded");
          meta.textContent = bits.join(" · ");
          if (n.data("topics")) {
            var t = document.createElement("div");
            t.className = "gx-tip-meta";
            t.textContent = n.data("topics");
            tip.appendChild(t);
          }
        }
        tip.insertBefore(meta, tip.children[1] || null);

        var pos = n.renderedPosition();
        var box = canvas.getBoundingClientRect();
        var rootBox = root.getBoundingClientRect();
        var x = box.left - rootBox.left + pos.x + 14;
        var y = box.top - rootBox.top + pos.y + 14;
        tip.style.transform = "translate(" + Math.round(x) + "px," + Math.round(y) + "px)";
        tip.classList.add("is-on");
      }

      /* M3.5.4 — nodes pulse as the brain is read. The ticker beside it
         keeps the overlay legible when a read lands off-screen or on an
         id with no node ("INDEX", a raw/ path). */
      var pulses = [];
      document.addEventListener("brain:reads", function (evt) {
        evt.detail.events.forEach(function (read) {
          (read.entity_ids || []).forEach(function (id) {
            var n = cy.getElementById(id);
            if (n.nonempty()) {
              pulses.push(id);
              n.flashClass("pulse", 1100);
            }
          });
          addTick(ticker, read);
        });
      });

      /* A live handle on the drawn graph: it makes the lens highlight
         and the activity overlay inspectable from the console. */
      window.brainExplorer = {
        cy: cy,
        data: data,
        applyLens: applyLens,
        clear: clearHighlight,
        pulses: pulses,
        highlighted: function () {
          return cy
            .nodes()
            .filter(function (n) {
              return !n.hasClass("dim");
            })
            .map(function (n) {
              return n.id();
            });
        }
      };
    }
  }

  /* Catalogs and identity files carry no topics, so they are genuine
     islands. cose strings islands out in a long row, which stretches the
     bounding box and squashes the connected core into a corner — so they
     get parked in a labelled column beside it instead. They stay part of
     the brain, they just stop distorting it. */
  function parkUnlinked(cy) {
    var loose = cy.nodes('[type="entity"]').filter(function (n) {
      return n.degree(false) === 0;
    });
    if (!loose.length) return;

    var core = cy.nodes().difference(loose);
    var box = core.length ? core.boundingBox() : { x2: 0, y1: 0, y2: 0 };
    var top = core.length ? (box.y1 + box.y2) / 2 - (loose.length - 1) * 17 : 0;

    loose.addClass("loose");
    loose.forEach(function (n, i) {
      n.position({ x: box.x2 + 110, y: top + i * 34 });
    });
    cy.fit(undefined, 22);
  }

  function addTick(host, read) {
    if (!host) return;
    var line = document.createElement("div");
    line.className = "gx-tick";
    var n = (read.entity_ids || []).length;
    line.textContent =
      read.at.slice(11, 19) + "  " + (read.endpoint || "read") + " · " + read.tier +
      " · " + n + (n === 1 ? " note" : " notes") + " · " + read.consumer;
    host.insertBefore(line, host.firstChild);
    while (host.children.length > 8) host.removeChild(host.lastChild);
  }

  function buildLegend(host, data) {
    var counts = {};
    data.nodes.forEach(function (n) {
      if (n.type === "entity") counts[n.kind] = (counts[n.kind] || 0) + 1;
    });
    Object.keys(counts)
      .sort(function (a, b) {
        return VIZ.kindRank(a) - VIZ.kindRank(b);
      })
      .forEach(function (kind) {
        var item = document.createElement("span");
        item.className = "gx-key-item";
        var swatch = document.createElement("span");
        swatch.className = "gx-swatch";
        swatch.style.background = VIZ.color(kind);
        item.appendChild(swatch);
        item.appendChild(document.createTextNode(kind + " "));
        var n = document.createElement("span");
        n.className = "gx-key-n";
        n.textContent = counts[kind];
        item.appendChild(n);
        host.appendChild(item);
      });
  }

  function buildPicker(picker, data) {
    data.lenses.forEach(function (l) {
      var opt = document.createElement("option");
      opt.value = l.id;
      opt.textContent = l.name + " (" + l.member_count + ")";
      picker.appendChild(opt);
    });
    if (!data.lenses.length) {
      picker.disabled = true;
      picker.options[0].textContent = "no lenses in the brain";
    }
  }

  document.addEventListener("DOMContentLoaded", function () {
    Array.prototype.forEach.call(document.querySelectorAll("[data-graph-explorer]"), mount);
  });
})();
