"""Deliberate-failure probe for layout_lint.

Written for cell 5, when the lint was taught that an `axis('off')` axes paints
no furniture. A check that quietly stops checking is worse than the noise it
replaced, so every case the lint is SUPPOSED to catch is asserted here
alongside the two it is now supposed to ignore.

Run:  py mockups/aspect_variants/probe_layout_lint.py
"""
import sys

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

sys.stdout.reconfigure(encoding="utf-8", errors="replace")
sys.path.insert(0, __file__.rsplit("\\", 1)[0])

from layout_lint import lint

CASES = []


def case(name, expect, builder, **kw):
    """`kw` is passed straight to lint(), for the opt-in checks."""
    CASES.append((name, expect, builder, kw))


# ── things the lint MUST still catch ─────────────────────────────────────────

def _overlapping_text():
    fig, ax = plt.subplots(figsize=(16, 9))
    ax.text(0.5, 0.5, "AAAAAAAAAAAAAAAA", fontsize=40, ha="center")
    ax.text(0.5, 0.5, "BBBBBBBBBBBBBBBB", fontsize=40, ha="center")
    return fig


def _clipped_text():
    fig, ax = plt.subplots(figsize=(16, 9))
    fig.text(1.4, 0.5, "OFF THE RIGHT EDGE", fontsize=20)
    return fig


def _tiny_type():
    fig, ax = plt.subplots(figsize=(16, 9))
    ax.text(0.5, 0.5, "MICROSCOPIC", fontsize=4)
    return fig


def _figure_text_over_a_real_plot():
    fig = plt.figure(figsize=(16, 9))
    ax = fig.add_axes([0.1, 0.1, 0.8, 0.8])
    ax.plot([0, 1], [0, 1])
    fig.text(0.5, 0.5, "STRAY FIGURE TEXT", fontsize=20, ha="center")
    return fig


def _ticks_on_a_VISIBLE_axes_still_counted():
    """The gate must key on axison, not on 'is this a canvas-style axes'.

    Asserted via CLIPPED rather than OVERLAP: a collision case has to actually
    achieve a collision, and the first version of this probe placed its two
    strings near each other without overlapping, so it reported a lint
    regression that did not exist. A tick label running off the canvas is
    unambiguous - it can only be reported if visible-axes ticks are still
    being scanned at all.
    """
    fig = plt.figure(figsize=(16, 9))
    ax = fig.add_axes([0.1, 0.1, 0.8, 0.8])
    ax.plot([0, 1], [0, 1])
    ax.set_xticks([1.0])
    ax.set_xticklabels(["A TICK LABEL SO WIDE IT LEAVES THE FIGURE ENTIRELY"],
                       fontsize=40)
    return fig


def _legend_on_an_axis_off_axes():
    """A legend is not axis furniture - it paints, so it must stay linted."""
    fig = plt.figure(figsize=(16, 9))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.plot([0, 1], [0, 1], label="TINY LEGEND LABEL")
    ax.legend(fontsize=4)
    ax.axis("off")
    return fig


def _annotation_on_an_axis_off_axes():
    """ax.text() paints on an axis-off canvas - verified by pixel diff."""
    fig = plt.figure(figsize=(16, 9))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.axis("off")
    ax.text(0.5, 0.5, "TINY ANNOTATION", fontsize=4)
    return fig


case("overlapping text", "OVERLAP", _overlapping_text)
case("text off the canvas", "CLIPPED", _clipped_text)
case("type under the floor", "TOO SMALL", _tiny_type)
case("figure text on a real plot", "TEXT OVER AXES", _figure_text_over_a_real_plot)
case("ticks on a VISIBLE axes", "CLIPPED", _ticks_on_a_VISIBLE_axes_still_counted)
case("legend on an axis-off axes", "TOO SMALL", _legend_on_an_axis_off_axes)
case("annotation on an axis-off axes", "TOO SMALL", _annotation_on_an_axis_off_axes)


# ── things the lint must now IGNORE ──────────────────────────────────────────

def _phantom_ticks_on_an_axis_off_canvas():
    fig = plt.figure(figsize=(16, 9))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.axis("off")
    return fig


def _figure_text_over_an_invisible_canvas():
    fig = plt.figure(figsize=(16, 9))
    ax = fig.add_axes([0, 0, 1, 1])
    ax.axis("off")
    fig.text(0.5, 0.95, "A TITLE ON AN INVISIBLE CANVAS", fontsize=24,
             ha="center")
    return fig


case("phantom ticks, axis off", None, _phantom_ticks_on_an_axis_off_canvas)
case("figure text over invisible canvas", None,
     _figure_text_over_an_invisible_canvas)


# ── the two opt-in checks, added 2026-09-11 ──────────────────────────────────
# Both came out of the pass map, where the lint reported six findings before
# and six after four measured geometry defects - it could see none of them.
# They are opt-in, so the probe has to ask for them explicitly.

def _text_crowding_the_canvas_edge():
    """Inside the frame, so CLIPPED stays silent - but 3px from it."""
    fig = plt.figure(figsize=(16, 9))
    fig.text(0.003, 0.5, "A CAPTION THAT RAN OUT OF CANVAS", fontsize=16)
    return fig


def _text_respecting_the_margin():
    fig = plt.figure(figsize=(16, 9))
    fig.text(0.04, 0.5, "A CAPTION WITH ROOM", fontsize=16)
    return fig


def _figure_text_on_a_painted_panel():
    """The case check 4 gives up on: an axis-off canvas with a real patch."""
    from matplotlib.patches import Rectangle
    fig = plt.figure(figsize=(16, 9))
    fig.patch.set_facecolor("#1A2332")
    ax = fig.add_axes([0.05, 0.15, 0.6, 0.7])
    ax.axis("off")
    ax.add_patch(Rectangle((0, 0), 1, 1, transform=ax.transAxes,
                           facecolor="#222E40", edgecolor="none"))
    fig.text(0.10, 0.84, "A LEGEND LABEL ON THE PANEL", fontsize=14,
             color="white")
    return fig


def _figure_text_below_a_painted_panel():
    from matplotlib.patches import Rectangle
    fig = plt.figure(figsize=(16, 9))
    fig.patch.set_facecolor("#1A2332")
    ax = fig.add_axes([0.05, 0.15, 0.6, 0.7])
    ax.axis("off")
    ax.add_patch(Rectangle((0, 0), 1, 1, transform=ax.transAxes,
                           facecolor="#222E40", edgecolor="none"))
    fig.text(0.10, 0.08, "A LEGEND LABEL BELOW IT", fontsize=14,
             color="white")
    return fig


case("text crowding the canvas edge", "MARGIN",
     _text_crowding_the_canvas_edge, margin=0.006)
case("text respecting the margin", None,
     _text_respecting_the_margin, margin=0.006)
case("figure text on a painted panel", "TEXT ON PANEL",
     _figure_text_on_a_painted_panel, patch_check=True)
case("figure text below a painted panel", None,
     _figure_text_below_a_painted_panel, patch_check=True)


if __name__ == "__main__":
    failures = 0
    for name, expect, builder, kw in CASES:
        fig = builder()
        found = lint(fig, delivery="laptop", **kw)
        plt.close(fig)
        if expect is None:
            ok = not found
            verdict = "silent" if ok else f"SPOKE UP: {found[:1]}"
        else:
            ok = any(expect in f for f in found)
            verdict = f"caught {expect!r}" if ok else f"MISSED {expect!r}: {found}"
        failures += not ok
        print(f"  [{'ok ' if ok else 'FAIL'}] {name:38s} {verdict}")

    print(f"\n{len(CASES) - failures}/{len(CASES)} probe cases behaved as "
          f"specified.")
    sys.exit(1 if failures else 0)
