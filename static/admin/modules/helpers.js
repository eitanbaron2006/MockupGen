/** The studio's small, self-contained helpers.
 *
 * Everything here is a pure function of its arguments: no page, no state, no
 * network. That is what makes them the first thing worth taking out of the
 * 8,000-line file -- they can be read, tested and reused without knowing
 * anything about the editor around them.
 */

export function escapeHtml(value) {
  const element = document.createElement("span");
  element.textContent = String(value || "");
  return element.innerHTML;
}

export function escapeAttr(value) {
  return escapeHtml(value).replace(/"/g, "&quot;").replace(/'/g, "&#39;");
}

export function clampStyleNumber(value, min, max, fallback) {
  const number = Number(value);
  if (!Number.isFinite(number)) return fallback;
  return Math.min(max, Math.max(min, number));
}

export function dataURLtoFile(dataurl, filename) {
  const arr = dataurl.split(",");
  const mime = arr[0].match(/:(.*?);/)[1];
  const bstr = atob(arr[1]);
  let n = bstr.length;
  const u8arr = new Uint8Array(n);
  while (n--) {
    u8arr[n] = bstr.charCodeAt(n);
  }
  return new File([u8arr], filename, { type: mime });
}

export function resolveFitMode(fitMode, artworkWidth, artworkHeight, frameWidth, frameHeight) {
  if (fitMode !== "auto") return fitMode;
  if (!artworkWidth || !artworkHeight || !frameWidth || !frameHeight) {
    return "cover";
  }
  const artworkRatio = artworkWidth / artworkHeight;
  const frameRatio = frameWidth / frameHeight;
  
  // Within 3% aspect ratio difference, use stretch
  if (Math.abs(artworkRatio - frameRatio) < 0.03) {
    return "stretch";
  }
  
  const getOrientation = (ratio) => {
    if (ratio > 1.15) return "landscape";
    if (ratio < 0.85) return "portrait";
    return "square";
  };
  
  const artOrientation = getOrientation(artworkRatio);
  const frameOrientation = getOrientation(frameRatio);
  
  if (artOrientation === frameOrientation) {
    return "cover";
  } else {
    return "stretch";
  }
}

export function getMatrix3d(w, h, p0, p1, p2, p3) {
  const x0 = p0.x, y0 = p0.y;
  const x1 = p1.x, y1 = p1.y;
  const x2 = p2.x, y2 = p2.y;
  const x3 = p3.x, y3 = p3.y;

  const dx1 = x1 - x2;
  const dx2 = x3 - x2;
  const dy1 = y1 - y2;
  const dy2 = y3 - y2;
  const dx3 = x0 - x1 + x2 - x3;
  const dy3 = y0 - y1 + y2 - y3;

  let a, b, c, d, e, f, g, h_coeff;

  const det = dx1 * dy2 - dx2 * dy1;
  if (Math.abs(det) < 1e-9) {
    a = x1 - x0;
    b = x3 - x0;
    c = x0;
    d = y1 - y0;
    e = y3 - y0;
    f = y0;
    g = 0;
    h_coeff = 0;
  } else {
    g = (dx3 * dy2 - dx2 * dy3) / det;
    h_coeff = (dx1 * dy3 - dx3 * dy1) / det;
    a = x1 - x0 + g * x1;
    b = x3 - x0 + h_coeff * x3;
    c = x0;
    d = y1 - y0 + g * y1;
    e = y3 - y0 + h_coeff * y3;
    f = y0;
  }

  const a_prime = a / w;
  const b_prime = b / h;
  const c_prime = c;
  const d_prime = d / w;
  const e_prime = e / h;
  const f_prime = f;
  const g_prime = g / w;
  const h_prime = h_coeff / h;

  return [
    a_prime, d_prime, 0, g_prime,
    b_prime, e_prime, 0, h_prime,
    0,       0,       1, 0,
    c_prime, f_prime, 0, 1
  ];
}

export function usableCorners(corners) {
  return Array.isArray(corners)
    && corners.length >= 4
    && corners.every((point) => point
      && Number.isFinite(Number(point.x))
      && Number.isFinite(Number(point.y)));
}

export function sliderStep(input) {
  const step = Number(input.step);
  return Number.isFinite(step) && step > 0 ? step : 1;
}

export function sliderDecimals(step) {
  const text = String(step);
  const dot = text.indexOf(".");
  return dot === -1 ? 0 : text.length - dot - 1;
}

export function confidenceLabel(value) {
  return value == null ? "" : `Confidence ${Math.round(value * 100)}%`;
}

export function cloneObject(value) {
  return JSON.parse(JSON.stringify(value));
}

export function statusClass(template) {
  return template.status === "active" ? "approved" : "review";
}

export function areaCorners(area) {
  if (area && Array.isArray(area.corners) && area.corners.length >= 4) return area.corners;
  if (!area) return [];
  return [
    { x: area.x, y: area.y },
    { x: area.x + area.width, y: area.y },
    { x: area.x + area.width, y: area.y + area.height },
    { x: area.x, y: area.y + area.height }
  ];
}
