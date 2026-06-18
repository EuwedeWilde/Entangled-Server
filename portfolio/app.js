const canvas = document.querySelector("canvas");
const ctx = canvas.getContext("2d");

const loader = document.getElementById("loader");
if (loader) {
  loader.addEventListener("animationend", (e) => {
    if (e.animationName === "loader-fade") loader.classList.add("done");
  });
}


// The DOM lens element — real glass via backdrop-filter.
const lens = document.querySelector(".lens");
const lensContent = document.querySelector(".lens-content");

// The element currently being magnified (whichever data-lens area the
// cursor is over). The clone is rebuilt whenever this changes.
let sourceArea = null;
let sourceVideo = null;
let cloneVideo = null;

function buildLensContent() {
  lensContent.innerHTML = "";
  sourceVideo = null;
  cloneVideo = null;
  if (!sourceArea) return;

  const clone = sourceArea.cloneNode(true);
  // Pin the clone to the source's on-page position so coordinates line up.
  const rect = sourceArea.getBoundingClientRect();
  clone.style.position = "absolute";
  clone.style.top = `${rect.top + window.scrollY}px`;
  clone.style.left = `${rect.left + window.scrollX}px`;
  clone.style.width = `${rect.width}px`;
  clone.style.height = `${rect.height}px`;
  clone.style.margin = "0";
  clone.removeAttribute("data-lens");

  // The landing-content uses mix-blend-mode: difference, which would blend
  // against nothing inside the lens. Neutralize it on the clone so the
  // magnified text keeps its real appearance over the video.
  const cloneContent = clone.querySelector(".landing-content");
  if (cloneContent) cloneContent.style.mixBlendMode = "normal";

  lensContent.appendChild(clone);

  // Keep a cloned video in rough sync with the original, if present.
  sourceVideo = sourceArea.querySelector("video");
  cloneVideo = clone.querySelector("video");
}
window.addEventListener("resize", buildLensContent);

// ---- Position side figures beside their anchor elements, auto-stacking ----
// Each .side-fig is absolutely positioned inside its .section. We set its top
// to align with the anchor element (data-anchor -> element id). If two figures
// on the same side+section would overlap, the later one is pushed down.
const GUTTER_BREAKPOINT = 1024;

// Remember where each figure originally sits in the DOM (its parent + the
// sibling it came before) so we can restore it when switching back to the
// wide gutter layout. Captured once, before anything moves.
const figHomes = new Map();
document.querySelectorAll(".side-fig").forEach((fig) => {
  figHomes.set(fig, { parent: fig.parentNode, nextSibling: fig.nextSibling });
});

function layoutSideFigures() {
  const figs = Array.from(document.querySelectorAll(".side-fig"));

  // Below the figure breakpoint the figures fold into normal flow (see the
  // matching @media rule in main.css). We physically move each figure to sit
  // directly AFTER its anchor paragraph so it reads as belonging to that text,
  // clear any inline `top` left over from the wide layout, and let CSS handle
  // the rest.
  if (window.innerWidth <= GUTTER_BREAKPOINT) {
    figs.forEach((fig) => {
      fig.style.top = "";
      const anchorId = fig.getAttribute("data-anchor");
      const anchor = anchorId ? document.getElementById(anchorId) : null;
      if (anchor && anchor.parentNode) {
        // Insert the figure right after its anchor element.
        anchor.parentNode.insertBefore(fig, anchor.nextSibling);
      }
    });
    return;
  }

  // Wide layout: make sure each figure is back in its original DOM home before
  // we position it absolutely in the gutter.
  figs.forEach((fig) => {
    const home = figHomes.get(fig);
    if (home && fig.parentNode !== home.parent) {
      home.parent.insertBefore(fig, home.nextSibling);
    }
  });

  const lowestBySideKey = new Map(); // key: section+side -> lowest occupied y
  const gap = 16; // px gap between stacked figures on the same side
  let sectionCounter = 0;
 
  figs.forEach((fig) => {
    const section = fig.closest(".section");
    if (!section) return;
    if (!section.dataset.sid) section.dataset.sid = `s${sectionCounter++}`;
 
    const anchorId = fig.getAttribute("data-anchor");
    const side = fig.getAttribute("data-side") || "right";
    const anchor = anchorId ? document.getElementById(anchorId) : null;
    if (!anchor) return;
 
    // Anchor top relative to the section (the figure's offset parent).
    const sectionTop = section.getBoundingClientRect().top + window.scrollY;
    const anchorTop = anchor.getBoundingClientRect().top + window.scrollY;
    let desiredTop = anchorTop - sectionTop;
 
    // Stack: never overlap the previous figure on the same section+side.
    const key = `${section.dataset.sid}-${side}`;
    const lowest = lowestBySideKey.get(key);
    if (lowest !== undefined && desiredTop < lowest + gap) {
      desiredTop = lowest + gap;
    }
 
    fig.style.top = `${desiredTop}px`;
    lowestBySideKey.set(key, desiredTop + fig.offsetHeight);
  });
}
window.addEventListener("load", layoutSideFigures);
window.addEventListener("resize", layoutSideFigures);
// Re-run once fonts settle, in case heading heights shift the anchor positions.
if (document.fonts && document.fonts.ready) {
  document.fonts.ready.then(layoutSideFigures);
}
 
// ---- Image sliders: auto-advance + clickable dots ----
function initSliders() {
  const sliders = document.querySelectorAll(".side-slider");
  sliders.forEach((slider) => {
    const slides = Array.from(slider.querySelectorAll(".slide"));
    if (slides.length <= 1) return; // nothing to slide
 
    const fig = slider.closest(".side-fig");
    const dotsBox = fig ? fig.querySelector(".slider-dots") : null;
    let current = slides.findIndex((s) => s.classList.contains("active"));
    if (current < 0) current = 0;
 
    // Build a dot button per slide.
    const dots = [];
    if (dotsBox) {
      slides.forEach((_, i) => {
        const dot = document.createElement("button");
        dot.type = "button";
        dot.setAttribute("aria-label", `Show image ${i + 1}`);
        if (i === current) dot.classList.add("active");
        dot.addEventListener("click", () => {
          show(i);
          restartTimer(); // reset the auto-advance after manual control
        });
        dotsBox.appendChild(dot);
        dots.push(dot);
      });
    }
 
    function show(idx) {
      slides[current].classList.remove("active");
      if (dots[current]) dots[current].classList.remove("active");
      current = (idx + slides.length) % slides.length;
      slides[current].classList.add("active");
      if (dots[current]) dots[current].classList.add("active");
      // If the lens is currently magnifying this slider, refresh its clone
      // so the magnified view shows the new slide.
      if (typeof sourceArea !== "undefined" && sourceArea === slider) {
        buildLensContent();
      }
    }
 
    let timer = null;
    function restartTimer() {
      clearInterval(timer);
      timer = setInterval(() => show(current + 1), 4000);
    }
    restartTimer();
  });
}
window.addEventListener("load", initSliders);


const pointer = {
  x: -200,
  y: -200,
};
const params = {
  pointsNumber: 30,
  widthFactor: 10,
  mouseThreshold: 1,
  spring: 2,
  friction: 0.24,
};

// 0 = full trail (no lens), 1 = full lens (no trail). Eased toward target.
let lensAmount = 0;
let lensTarget = 0;

// Cursor size multiplier: eases to a smaller value when hovering a link.
let pointerScale = 1;
let pointerScaleTarget = 1;

const trail = new Array(params.pointsNumber);
for (let i = 0; i < params.pointsNumber; i++) {
  trail[i] = {
    x: pointer.x,
    y: pointer.y,
    dx: 0,
    dy: 0,
  };
}

window.addEventListener("click", (e) => {
  updateMousePosition(e.clientX, e.clientY);
});
window.addEventListener("mousemove", (e) => {
  updateMousePosition(e.clientX, e.clientY);
  updateLensTarget(e.clientX, e.clientY);
});
window.addEventListener("touchmove", (e) => {
  const t = e.targetTouches[0];
  updateMousePosition(t.clientX, t.clientY);
  updateLensTarget(t.clientX, t.clientY);
});

function updateMousePosition(eX, eY) {
  pointer.x = eX;
  pointer.y = eY;
}

// Decide whether the pointer is over a "lensable" area, and if so which one.
// Any element (or ancestor) carrying data-lens counts. When the hovered area
// changes, rebuild the magnified clone from the new element.
function updateLensTarget(eX, eY) {
  const el = document.elementFromPoint(eX, eY);
  const area = el ? el.closest("[data-lens]") : null;
  if (area !== sourceArea) {
    sourceArea = area;
    buildLensContent();
  }
  lensTarget = area ? 1 : 0;

  // Shrink the cursor when hovering a link (or anything inside one).
  const overLink = el ? el.closest("a, .slider-dots") !== null : false;
  pointerScaleTarget = overLink ? 0.1 : 1;
}

setupCanvas();
update(0);
window.addEventListener("resize", setupCanvas);

function update(t) {
  ctx.clearRect(0, 0, canvas.width, canvas.height);

  // Ease the lens amount toward its target for a smooth morph.
  lensAmount += (lensTarget - lensAmount) * 0.12;

  // Ease the cursor size toward its target (shrinks over links).
  pointerScale += (pointerScaleTarget - pointerScale) * 0.2;

  // --- Physics: lead point follows pointer, rest follow the chain ---
  trail.forEach((p, pIdx) => {
    const prev = pIdx === 0 ? pointer : trail[pIdx - 1];
    const spring = pIdx === 0 ? 0.4 * params.spring : params.spring;
    p.dx += (prev.x - p.x) * spring;
    p.dy += (prev.y - p.y) * spring;
    p.dx *= params.friction;
    p.dy *= params.friction;
    p.x += p.dx;
    p.y += p.dy;
  });

  // --- Canvas tail: fades out as the lens takes over ---
  const tailAlpha = 1 - lensAmount;
  if (tailAlpha > 0.01) {
    ctx.globalAlpha = tailAlpha;
    ctx.strokeStyle = "white";
    ctx.fillStyle = "white";
    ctx.lineCap = "round";
    ctx.lineJoin = "round";
    ctx.lineWidth = params.widthFactor * 4 * pointerScale;

    ctx.beginPath();
    ctx.moveTo(trail[0].x, trail[0].y);
    for (let i = 1; i < trail.length - 1; i++) {
      const xc = 0.5 * (trail[i].x + trail[i + 1].x);
      const yc = 0.5 * (trail[i].y + trail[i + 1].y);
      ctx.quadraticCurveTo(trail[i].x, trail[i].y, xc, yc);
    }
    ctx.lineTo(trail[trail.length - 1].x, trail[trail.length - 1].y);
    ctx.stroke();

    const r = params.widthFactor * 2 * pointerScale;
    for (let i = 0; i < trail.length; i++) {
      ctx.beginPath();
      ctx.arc(trail[i].x, trail[i].y, r, 0, Math.PI * 2);
      ctx.fill();
    }
    ctx.globalAlpha = 1;
  }

  // --- DOM lens: grows in as the amount rises ---
  // Position it at the lead point (trail[0]) so it inherits the spring lag.
  const lead = trail[0];
  const baseSize = params.widthFactor * 4; // matches the trail thickness
  const maxSize = 160; // diameter of the full glass lens
  const size = baseSize + (maxSize - baseSize) * lensAmount;
  const r = size / 2;

  lens.style.width = `${size}px`;
  lens.style.height = `${size}px`;
  lens.style.transform = `translate(${lead.x - r}px, ${lead.y - r}px)`;
  lens.style.opacity = lensAmount;

  // True magnification: scale the cloned content and shift it so the page
  // point under the lens centre (lead.x, lead.y) lands at the lens centre.
  // The clone is pinned at page coords, so we work in page space then undo
  // the lens's own translate by adding r.
  const zoom = 1.6;
  const pageX = lead.x + window.scrollX;
  const pageY = lead.y + window.scrollY;
  const tx = -pageX * zoom + r;
  const ty = -pageY * zoom + r;
  lensContent.style.transform = `translate(${tx}px, ${ty}px) scale(${zoom})`;

  // Keep the cloned video roughly in sync with the real one.
  if (cloneVideo && sourceVideo && !sourceVideo.paused) {
    if (Math.abs(cloneVideo.currentTime - sourceVideo.currentTime) > 0.08) {
      cloneVideo.currentTime = sourceVideo.currentTime;
    }
  }

  window.requestAnimationFrame(update);
}

// Logo / title link scrolls back to the top of the page.
const navHome = document.querySelector(".nav-home");
if (navHome) {
  navHome.addEventListener("click", (e) => {
    e.preventDefault();
    window.scrollTo({ top: 0, behavior: "smooth" });
  });
}

function setupCanvas() {
  canvas.width = window.innerWidth;
  canvas.height = window.innerHeight;
}