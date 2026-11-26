/*
 * diagram-tooltip.js: a themed tooltip for the /assess and /fleet diagrams (#1360, #1312).
 *
 * Include it once on a host page that shows the diagrams inline (<svg> in the page) or
 * through <object type="image/svg+xml">. An <img> embed shows no details: the browser draws
 * it as a picture with no events, so neither this helper nor the native <title> can reach it.
 *
 *   <script src="diagram-tooltip.js"></script>
 *
 * On load it attaches to the page and to every <object> holding an SVG; call
 * DiagramTooltip.attach(element) for diagrams added later, and pass
 * {format: function (part) { return "text"; }} to word the tooltip yourself.
 *
 * A part is the nearest element (below the root <svg>) with a <title> child. Its tooltip
 * lists the part's data-* attributes, one "name: value" line each. It leaves out a code that
 * has a readable twin on the same part (data-category beside data-category-label, data-tag
 * beside data-tag-title) and the model ids data-row and data-condition, which the readable
 * data-practice and data-gate name; state codes with no twin (data-cell="not_measured",
 * data-status="orphan") stay, since the muted <title> no longer says them. A part with no
 * data attributes shows its <title> text instead. Mouse and pen show it on hover and hide it
 * on pointer leave.
 * Touch shows it on the first tap and hides it on a second tap, a tap elsewhere or Escape.
 * On a linked part (inside an <a>), a mouse click follows the link; on touch the first tap
 * shows the tooltip and the second follows the link.
 *
 * No dependencies, no network: plain browser APIs only. Colours follow the viewer's colour
 * scheme; a host overrides them with --diagram-tooltip-bg, --diagram-tooltip-fg and
 * --diagram-tooltip-border.
 */
(function () {
  "use strict";

  var STYLE_ID = "diagram-tooltip-style";
  var PARKED = "data-tooltip-parked"; // a muted <title>'s text, never listed as a value
  var CSS =
    ".diagram-tooltip{position:fixed;z-index:2147483647;max-width:22rem;padding:6px 9px;" +
    "border-radius:6px;font:12px/1.4 -apple-system,BlinkMacSystemFont,'Segoe UI',Helvetica,Arial,sans-serif;" +
    "white-space:pre-line;pointer-events:none;" +
    "background:var(--diagram-tooltip-bg,#ffffff);color:var(--diagram-tooltip-fg,#1d2321);" +
    "border:1px solid var(--diagram-tooltip-border,#d9dedb);box-shadow:0 2px 8px rgba(0,0,0,.18)}" +
    ".diagram-tooltip[hidden]{display:none}" +
    "@media (prefers-color-scheme: dark){.diagram-tooltip{" +
    "background:var(--diagram-tooltip-bg,#1a201e);color:var(--diagram-tooltip-fg,#e6ebe8);" +
    "border-color:var(--diagram-tooltip-border,#2c3532)}}";

  var tip = null;       // the one tooltip element, in the host page
  var shown = null;     // the part whose tooltip is showing
  var touching = false; // the last pointer was a finger
  var blockClick = false; // the tap that showed a linked part's tooltip must not follow it
  // Model ids a readable attribute on the same part names (tests/svg_data_attrs.py ID_KEYS).
  var IDS = ["condition", "row"];
  // A code is left out when the part carries its readable twin: data-<key>-label or -title.
  var TWINS = ["-label", "-title"];

  function ensureTip() {
    if (tip) {
      return tip;
    }
    if (!document.getElementById(STYLE_ID)) {
      var style = document.createElement("style");
      style.id = STYLE_ID;
      style.textContent = CSS;
      document.head.appendChild(style);
    }
    tip = document.createElement("div");
    tip.className = "diagram-tooltip";
    tip.setAttribute("role", "tooltip");
    tip.hidden = true;
    document.body.appendChild(tip);
    return tip;
  }

  function titleOf(el) {
    for (var child = el.firstElementChild; child; child = child.nextElementSibling) {
      if (child.localName === "title") {
        return child;
      }
    }
    return null;
  }

  // The nearest element at or above target, below the root <svg>, with a <title> child.
  function partOf(target) {
    for (var el = target; el && el.ownerSVGElement; el = el.parentNode) {
      if (titleOf(el)) {
        return el;
      }
    }
    return null;
  }

  function linkOf(part) {
    return part.closest ? part.closest("a") : null;
  }

  function hidden(part, key) {
    if (IDS.indexOf(key) >= 0) {
      return true;
    }
    for (var i = 0; i < TWINS.length; i++) {
      if (part.hasAttribute("data-" + key + TWINS[i])) {
        return true;
      }
    }
    return false;
  }

  // The part's data-* attributes, one "name: value" line each, an id or a twinned code left
  // out (hidden); its <title> text without any.
  function defaultFormat(part) {
    var lines = [];
    for (var i = 0; i < part.attributes.length; i++) {
      var attr = part.attributes[i];
      if (attr.name.indexOf("data-") === 0 && attr.name !== PARKED &&
          !hidden(part, attr.name.slice(5))) {
        lines.push(attr.name.slice(5).replace(/-/g, " ") + ": " + attr.value);
      }
    }
    if (lines.length) {
      return lines.join("\n");
    }
    var title = titleOf(part);
    return part.getAttribute(PARKED) || (title ? title.textContent : "");
  }

  // While our tooltip shows, the native <title> tooltip would show too: park its text.
  function muteTitle(part) {
    var title = titleOf(part);
    if (title && title.textContent) {
      part.setAttribute(PARKED, title.textContent);
      title.textContent = "";
    }
  }

  function restoreTitle(part) {
    var title = titleOf(part);
    if (title && part.hasAttribute(PARKED)) {
      title.textContent = part.getAttribute(PARKED);
      part.removeAttribute(PARKED);
    }
  }

  function place(x, y) {
    var box = tip.getBoundingClientRect();
    var left = Math.min(x + 12, window.innerWidth - box.width - 8);
    var top = y + 16 + box.height > window.innerHeight ? y - box.height - 12 : y + 16;
    tip.style.left = Math.max(8, left) + "px";
    tip.style.top = Math.max(8, top) + "px";
  }

  function show(part, x, y, format) {
    ensureTip();
    if (shown && shown !== part) {
      hide();
    }
    tip.textContent = format(part);
    tip.hidden = false;
    if (shown !== part) {
      muteTitle(part);
    }
    shown = part;
    place(x, y);
  }

  function hide() {
    if (shown) {
      restoreTitle(shown);
      shown = null;
    }
    if (tip) {
      tip.hidden = true;
    }
  }

  // One handler per event, however many roots it bubbles through: a host that calls
  // attach(svg) on an SVG inside an already attached page would otherwise show the tooltip on
  // the inner listener and hide it again on the document's (the second-tap rule).
  function on(root, type, handler, options) {
    root.addEventListener(type, function (event) {
      if (event.__diagramTooltip) {
        return;
      }
      event.__diagramTooltip = true;
      handler(event);
    }, options);
  }

  // offset() turns an event's coordinates into the host page's: zero for an inline SVG, the
  // <object>'s position for one inside it.
  // formatOf() returns the root's current formatter, so a later attach(root, {format}) on a
  // root already attached takes effect without a second set of listeners.
  function listen(root, formatOf, offset) {
    on(root, "touchstart", function () { touching = true; }, {passive: true});
    on(root, "pointerdown", function (event) {
      touching = event.pointerType === "touch";
    });
    on(root, "pointermove", function (event) {
      if (event.pointerType === "touch") {
        return;
      }
      var part = partOf(event.target);
      var at = offset();
      if (part) {
        show(part, event.clientX + at.x, event.clientY + at.y, formatOf());
      } else if (shown) {
        hide();
      }
    });
    on(root, "pointerleave", function (event) {
      if (event.pointerType !== "touch") {
        hide();
      }
    });
    on(root, "pointerout", function (event) {
      if (event.pointerType === "touch" || !shown) {
        return;
      }
      if (partOf(event.relatedTarget) !== shown) {
        hide();
      }
    });
    // A tap is read on pointerup, not click: iOS Safari fires no click delegated from a
    // non-interactive SVG element.
    on(root, "pointerup", function (event) {
      if (event.pointerType !== "touch") {
        return;
      }
      var part = partOf(event.target);
      blockClick = false;
      if (!part) {
        hide();
        return;
      }
      if (shown === part) {
        hide(); // the second tap dismisses, and on a link follows it
        return;
      }
      blockClick = Boolean(linkOf(part)); // the first tap on a link only shows the tooltip
      var at = offset();
      show(part, event.clientX + at.x, event.clientY + at.y, formatOf());
    });
    on(root, "click", function (event) {
      if (!touching) {
        return; // a mouse click follows a link, as it would without the helper
      }
      if (blockClick) {
        event.preventDefault();
      }
      blockClick = false;
    });
    on(root, "keydown", function (event) {
      if (event.key === "Escape") {
        hide();
      }
    });
  }

  function attachObject(object, formatOf) {
    function hook() {
      var doc = object.contentDocument;
      if (!doc || doc.__diagramTooltip) {
        return;
      }
      doc.__diagramTooltip = true;
      listen(doc, function () { return object.__diagramTooltipFormat || formatOf(); }, function () {
        var box = object.getBoundingClientRect();
        return {x: box.left, y: box.top};
      });
    }
    object.addEventListener("load", hook);
    hook();
  }

  function attach(root, options) {
    var target = root || document;
    if (options && options.format) {
      target.__diagramTooltipFormat = options.format; // also updates a root already attached
    }
    function formatOf() { return target.__diagramTooltipFormat || defaultFormat; }
    if (target.localName === "object") {
      attachObject(target, formatOf);
      return;
    }
    if (!target.__diagramTooltip) {
      target.__diagramTooltip = true;
      listen(target, formatOf, function () { return {x: 0, y: 0}; });
    }
    var objects = target.querySelectorAll ? target.querySelectorAll('object[type="image/svg+xml"]') : [];
    for (var i = 0; i < objects.length; i++) {
      attachObject(objects[i], formatOf);
    }
  }

  window.DiagramTooltip = {attach: attach, hide: hide, format: defaultFormat};

  if (document.readyState === "loading") {
    document.addEventListener("DOMContentLoaded", function () { attach(document); });
  } else {
    attach(document);
  }
})();
