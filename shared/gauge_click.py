"""Click-on-image for the Team Profile page.

A static custom component of our own: `gauge_click_frontend/index.html`
shows a PNG and lays one invisible, hoverable region over each gauge's grid
cell. A click sends back the gauge's KEY (not pixels) with a fresh token;
the page maps the key to the drill it already knows how to do.

No third-party package and no build step. The message protocol was read
from the installed Streamlit 1.53.1 bundle (the parent hides the iframe
until `streamlit:componentReady`, and drops any message without an
`isStreamlitMessage` property); it is the mechanism every published
component uses, but not a documented contract for raw HTML, so a Streamlit
upgrade means opening the page once and clicking.

Regions are fractions of the image: (x, y) the top-left corner, (w, h) the
size, all in [0, 1]. The chart's `fig.tp_gauge_boxes` are matplotlib
figure fractions with the origin at the BOTTOM left; `regions_from_boxes`
does the flip. The figure is saved with `bbox_inches=None` (the shape
contract), so figure fraction and image fraction are the same thing.
"""
import os

import streamlit.components.v1 as components

_FRONTEND = os.path.join(os.path.dirname(os.path.abspath(__file__)), "gauge_click_frontend")

# Registered once per process. `path` must be absolute; the component's
# full name becomes "shared.gauge_click.gauge_click" from this module.
_gauge_click = components.declare_component("gauge_click", path=_FRONTEND)


def regions_from_boxes(boxes, specs, mode="rank"):
    """One region per gauge: `boxes` are the chart's (x0, y0, x1, y1) figure
    fractions, `specs` the GaugeSpecs in the same order. The label is what a
    screen reader announces for the region: the gauge's name and standing."""
    out = []
    for (x0, y0, x1, y1), spec in zip(boxes, specs):
        read = spec.standing.readout + ("" if mode == "rank" else " pctl")
        out.append({
            "key": spec.key,
            "label": f"{spec.label}, {read}",
            "x": float(x0), "y": float(1.0 - y1),
            "w": float(x1 - x0), "h": float(y1 - y0),
        })
    return out


def gauge_click(png, regions, *, ratio, max_width=None, selected=None, key=None):
    """Show `png` with clickable `regions` over it. Returns the LAST click as
    {'key': gauge key, 't': token} - it persists across reruns, so a caller
    keeps the token it has acted on and ignores a repeat - or None.

    ratio: image height / width, so the frame is sized before the PNG decodes.
    max_width: cap in CSS px (the portrait cuts); None fills the column.
    selected: the key of the gauge open on the level below, kept outlined.
    """
    return _gauge_click(png=png, regions=list(regions), ratio=float(ratio),
                        max_width=max_width, selected=selected, key=key, default=None)
