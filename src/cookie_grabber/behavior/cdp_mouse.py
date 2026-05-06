from __future__ import annotations

import random
import time

from playwright.sync_api import CDPSession, Page

from cookie_grabber.behavior.bezier import bezier_curve_points


def _cdp_session(page: Page) -> CDPSession:
    return page.context.new_cdp_session(page)


def human_mouse_move(page: Page, x: float, y: float) -> None:
    vp = page.viewport_size or {"width": 1280, "height": 720}
    start_x = random.uniform(0, vp["width"])
    start_y = random.uniform(0, vp["height"])
    cdp = _cdp_session(page)
    for px, py in bezier_curve_points(start_x, start_y, x, y, steps=random.randint(18, 35)):
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
        time.sleep(random.uniform(0.05, 0.25))


def move_mouse_path(page: Page, points: list[tuple[float, float]]) -> None:
    if not points:
        return
    cdp = _cdp_session(page)
    for px, py in points:
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
        time.sleep(random.uniform(0.003, 0.014))


def cdp_click(page: Page, x: float, y: float) -> None:
    human_mouse_move(page, x, y)
    cdp = _cdp_session(page)
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
