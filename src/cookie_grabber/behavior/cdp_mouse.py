from __future__ import annotations

import json
import random
import time

from playwright.sync_api import CDPSession, Page

from cookie_grabber.behavior.bezier import bezier_curve_points

# Выполняется при каждой навигации: индикатор позиции синтетической мыши (CDP не рисует ОС-курсор).
_MOUSE_OVERLAY_INIT_JS = """
(() => {
  const KEY = "__cgMouseOverlay";
  globalThis.__cgCursorMove = (x, y) => {
    let st = globalThis[KEY];
    if (!st) {
      const el = document.createElement("div");
      el.setAttribute("data-cg-overlay", "cursor");
      el.style.cssText =
        "position:fixed;left:0;top:0;width:20px;height:20px;border-radius:50%;" +
        "border:2px solid rgba(255,85,85,0.95);pointer-events:none;z-index:2147483647;" +
        "transform:translate(0px,0px);margin:-10px 0 0 -10px;box-sizing:border-box;" +
        "box-shadow:0 0 3px rgba(0,0,0,.35);display:none;";
      const root = document.documentElement || document.body;
      if (!root) return;
      root.appendChild(el);
      st = { el };
      globalThis[KEY] = st;
    }
    st.el.style.display = "block";
    st.el.style.transform = "translate(" + x + "px," + y + "px)";
  };
})();
""".strip()


def _cdp_session(page: Page) -> CDPSession:
    return page.context.new_cdp_session(page)


def install_synthetic_mouse_overlay(page: Page) -> None:
    """Подключает отрисовку позиции синтетической мыши в документе (до первого перехода по URL)."""
    page.add_init_script(_MOUSE_OVERLAY_INIT_JS)
    setattr(page, "_cg_mouse_overlay", True)


def _overlay_enabled(page: Page) -> bool:
    return bool(getattr(page, "_cg_mouse_overlay", False))


def _update_mouse_overlay(page: Page, cdp: CDPSession, x: float, y: float) -> None:
    if not _overlay_enabled(page):
        return
    try:
        expr = (
            "typeof __cgCursorMove==='function'&&__cgCursorMove("
            f"{json.dumps(float(x))},{json.dumps(float(y))})"
        )
        cdp.send("Runtime.evaluate", {"expression": expr})
    except Exception:
        pass


def human_mouse_move(page: Page, x: float, y: float) -> None:
    vp = page.viewport_size or {"width": 1280, "height": 720}
    start_x = random.uniform(0, vp["width"])
    start_y = random.uniform(0, vp["height"])
    cdp = _cdp_session(page)
    show = _overlay_enabled(page)
    curve = list(bezier_curve_points(start_x, start_y, x, y, steps=random.randint(18, 35)))
    last_i = len(curve) - 1
    for i, (px, py) in enumerate(curve):
        cdp.send(
            "Input.dispatchMouseEvent",
            {
                "type": "mouseMoved",
                "x": px,
                "y": py,
                "modifiers": 0,
                "pointerType": "mouse",
            },
        )
        if show and (i % 4 == 0 or i == last_i):
            _update_mouse_overlay(page, cdp, px, py)
        time.sleep(random.uniform(0.002, 0.012))
    mid_stops = random.randint(1, 3)
    for _ in range(mid_stops):
        jx = x + random.uniform(-30, 30)
        jy = y + random.uniform(-20, 20)
        cdp.send(
            "Input.dispatchMouseEvent",
            {
                "type": "mouseMoved",
                "x": jx,
                "y": jy,
                "modifiers": 0,
                "pointerType": "mouse",
            },
        )
        if show:
            _update_mouse_overlay(page, cdp, jx, jy)
        time.sleep(random.uniform(0.05, 0.25))


def move_mouse_path(page: Page, points: list[tuple[float, float]]) -> None:
    if not points:
        return
    cdp = _cdp_session(page)
    show = _overlay_enabled(page)
    last_i = len(points) - 1
    for i, (px, py) in enumerate(points):
        cdp.send(
            "Input.dispatchMouseEvent",
            {
                "type": "mouseMoved",
                "x": px,
                "y": py,
                "modifiers": 0,
                "pointerType": "mouse",
            },
        )
        if show and (i % 3 == 0 or i == last_i):
            _update_mouse_overlay(page, cdp, px, py)
        time.sleep(random.uniform(0.003, 0.014))


def cdp_click(page: Page, x: float, y: float) -> None:
    human_mouse_move(page, x, y)
    cdp = _cdp_session(page)
    if _overlay_enabled(page):
        _update_mouse_overlay(page, cdp, x, y)
    cdp.send(
        "Input.dispatchMouseEvent",
        {
            "type": "mousePressed",
            "x": x,
            "y": y,
            "button": "left",
            "buttons": 1,
            "clickCount": 1,
            "modifiers": 0,
            "pointerType": "mouse",
        },
    )
    time.sleep(random.uniform(0.05, 0.18))
    cdp.send(
        "Input.dispatchMouseEvent",
        {
            "type": "mouseReleased",
            "x": x,
            "y": y,
            "button": "left",
            "buttons": 0,
            "clickCount": 1,
            "modifiers": 0,
            "pointerType": "mouse",
        },
    )


def cdp_mouse_wheel(page: Page, delta_y: float) -> None:
    """Колесо мыши в случайной точке viewport (для скролла страницы)."""
    vp = page.viewport_size or {"width": 1280, "height": 720}
    x = random.uniform(vp["width"] * 0.2, vp["width"] * 0.8)
    y = random.uniform(vp["height"] * 0.25, vp["height"] * 0.75)
    cdp = _cdp_session(page)
    cdp.send(
        "Input.dispatchMouseEvent",
        {
            "type": "mouseWheel",
            "x": x,
            "y": y,
            "deltaX": 0.0,
            "deltaY": float(delta_y),
            "modifiers": 0,
            "pointerType": "mouse",
        },
    )
    if _overlay_enabled(page):
        _update_mouse_overlay(page, cdp, x, y)
    time.sleep(random.uniform(0.02, 0.08))
