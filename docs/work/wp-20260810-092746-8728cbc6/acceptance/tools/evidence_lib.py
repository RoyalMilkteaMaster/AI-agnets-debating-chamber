"""Ticket 09 驗收用的讀法工具：只讀公開可見行為，不斷言實作字串。

由 :mod:`gen_evidence` 匯入，兩個檔案放在一起；重跑方式見 ``rerun.sh``。
這裡不碰產品程式碼，也不寫任何檔案。
"""

import hashlib
import re
from html.parser import HTMLParser

VOID_TAGS = frozenset(
    "area base br col embed hr img input link meta param source track wbr".split()
)


class ControlReader(HTMLParser):
    """讀出一份 HTML 裡的導覽群組與按鈕，照讀者遇到的順序。

    只看語意屬性（``aria-label``、``role``、``aria-disabled``、``aria-current``、
    ``href``）與文字，不看 class、不看巢狀層數。
    """

    def __init__(self, region=None):
        super().__init__(convert_charrefs=True)
        self.region = region  # 只讀這個標籤名的子樹（例：header）
        self.navs = []          # [{"label":…, "items":[…]}]
        self.controls = []      # 依文件順序的互動控制：("nav-item"|"button", 標籤)
        self._nav_stack = []
        self._item = None
        self._button = None
        self._in_region = region is None
        self._region_depth = None

    # -- region gate --------------------------------------------------------
    def handle_starttag(self, tag, attrs):
        attributes = dict(attrs)
        if self.region is not None and tag == self.region and self._region_depth is None:
            self._region_depth = 0
            self._in_region = True
            return
        if self._region_depth is not None and tag not in VOID_TAGS:
            self._region_depth += 1
        if not self._in_region:
            return
        if tag == "nav":
            self._nav_stack.append(
                {"label": attributes.get("aria-label", ""), "items": []}
            )
            return
        if tag == "a":
            self._item = {
                "kind": "link",
                "href": attributes.get("href"),
                "current": attributes.get("aria-current"),
                "label": "",
            }
            return
        if tag == "span" and attributes.get("role") == "link":
            self._item = {
                "kind": "disabled" if attributes.get("aria-disabled") == "true" else "link",
                "href": attributes.get("href"),
                "current": attributes.get("aria-current"),
                "label": "",
            }
            return
        if tag == "button":
            self._button = {"label": "", "disabled": "disabled" in attributes}

    def handle_endtag(self, tag):
        if not self._in_region:
            if self._region_depth is not None and tag not in VOID_TAGS:
                self._region_depth -= 1
            return
        if tag == "nav" and self._nav_stack:
            self.navs.append(self._nav_stack.pop())
        elif tag in ("a", "span") and self._item is not None:
            self._item["label"] = self._item["label"].strip()
            if self._nav_stack:
                self._nav_stack[-1]["items"].append(self._item)
                self.controls.append(("nav-item", self._item["label"]))
            self._item = None
        elif tag == "button" and self._button is not None:
            self._button["label"] = self._button["label"].strip()
            self.controls.append(("button", self._button["label"]))
            self._button = None
        if self._region_depth is not None and tag not in VOID_TAGS:
            self._region_depth -= 1
            if self._region_depth <= 0 and tag == self.region:
                self._in_region = False
                self._region_depth = None

    def handle_data(self, data):
        if not self._in_region:
            return
        if self._item is not None:
            self._item["label"] += data
        elif self._button is not None:
            self._button["label"] += data


def read_controls(body, region=None):
    reader = ControlReader(region=region)
    reader.feed(body)
    return reader


def header_of(body):
    """頁面 header 的 HTML 片段（找不到就回空字串）。"""
    found = re.search(r"<header\b.*?</header>", body, re.DOTALL)
    return found.group(0) if found else ""


def nav_named(reader, label):
    for nav in reader.navs:
        if nav["label"] == label:
            return nav
    return None


def labels_of(nav):
    return [item["label"] for item in nav["items"]]


def sha256_of(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


# -- WCAG 對比度：獨立實作一次，不呼叫受測程式 --------------------------------


def _srgb_channel(value):
    channel = value / 255.0
    if channel <= 0.04045:
        return channel / 12.92
    return ((channel + 0.055) / 1.055) ** 2.4


def relative_luminance(hex_colour):
    colour = hex_colour.lstrip("#")
    red, green, blue = [int(colour[index:index + 2], 16) for index in (0, 2, 4)]
    return (
        0.2126 * _srgb_channel(red)
        + 0.7152 * _srgb_channel(green)
        + 0.0722 * _srgb_channel(blue)
    )


def contrast_ratio(foreground, background):
    first = relative_luminance(foreground)
    second = relative_luminance(background)
    lighter, darker = max(first, second), min(first, second)
    return (lighter + 0.05) / (darker + 0.05)
