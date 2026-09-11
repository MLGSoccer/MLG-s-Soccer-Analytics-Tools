"""Numeric layout lint for matplotlib figures.

Catches the mechanical class of failure - overlap, clipping, illegible type -
so a design critique isn't spent reporting bugs. Uses renderer bounding boxes,
so it measures what actually got drawn, not what the code intended.

Counterpart to PodcastShorts/pipeline/chart_templates/dev/layout_lint.js. The
two are DELIBERATELY separate implementations: the substrates differ (renderer
extents vs getBoundingClientRect) and there is no sharing the measurement. What
IS shared is the policy, and it has drifted before - see the coverage table in
the chart-critique skill for which checks exist on which surface.

matplotlib is the easier substrate. The JS lint needs canvas glyph measurement
because the DOM hands back CONTAINER boxes, which produced 18 false collisions
in one library. Text.get_window_extent() returns the text's own extent, so that
whole class of error does not arise here.

Clean lint is NECESSARY, NOT SUFFICIENT. Read the PNG afterwards.
"""
import matplotlib
matplotlib.use("Agg")

# Minimum legible type, as a target size in the DELIVERED image.
#
# Legibility is angular: it depends on how big the thing is when a human looks
# at it, not on how many pixels it has. A 16:9 chart plays on a laptop or a TV
# where 16px against a 1920-wide frame is comfortable. A vertical plays on a
# phone, where 1080 source px land in roughly 400 CSS px, so anything under
# ~26px arrives smaller than 10px.
#
# DELIVERY IS NOT INFERRABLE FROM THE FIGURE'S SHAPE. The JS lint learned this
# the hard way: a 9:8 tile is wider than tall but plays as half a phone short,
# and inferring from w > h handed it the laptop floor and waved through type
# nobody could read. A 10x10 square pitch chart has the opposite problem. So
# callers pass delivery explicitly and the default is the lenient one, which
# fails loud rather than silently strict.
DELIVERY = {
    #          target_px  delivered_px
    "laptop":  (16,       1920),   # 16:9 parent video, laptop/TV
    "phone":   (26,       1080),   # 9:16 or 9:8, watched on a phone
}


def floor_pt(fig, delivery="laptop"):
    """Point size below which type is illegible in the delivered image.

    A flat pt floor is wrong because it ignores figsize: 9pt on a
    figsize=(6,4) figure is more than twice the relative size of 9pt on a
    figsize=(16,9) one. Derive it instead.

        text_px / image_px = pt / (72 * fig_width_in)

    so to land at `target_px` once the image is shown `delivered_px` wide:

        pt >= target_px * 72 * fig_width_in / delivered_px

    Worked examples:
        figsize=(16, 9)  laptop ->  9.6pt
        figsize=(10, 7)  laptop ->  6.0pt   (flat 9pt was FALSELY strict)
        figsize=(9, 16)  phone  -> 15.6pt   (flat 9pt was FAR too lenient)
    """
    if delivery not in DELIVERY:
        raise ValueError(
            f"delivery must be one of {sorted(DELIVERY)}, got {delivery!r}")
    target_px, delivered_px = DELIVERY[delivery]
    w_in = float(fig.get_size_inches()[0])
    return target_px * 72.0 * w_in / delivered_px


# Minimum share of the smaller box an overlap must cover before it counts.
# Two labels whose boxes graze by a few px are geometry, not a collision.
SLIVER = 0.35


def _visible(artist):
    if not artist.get_visible():
        return False
    try:
        if float(artist.get_alpha() or 1.0) <= 0.01:
            return False
    except (TypeError, ValueError):
        pass
    return True


def _tick_in_view(label, axis):
    """True if a tick label is inside its axes limits, so it is really drawn.

    matplotlib keeps tick objects for ticks OUTSIDE the current view and they
    still report get_visible() == True - they are simply never rendered. Taking
    them at their word produced a steady stream of false overlaps: an xG race
    sets ylim to max_xg * 1.05, which almost never lands on a tick, so a
    phantom tick sat just above every chart and collided with the goal labels
    placed up there. Barcelona v Rayo reported "Sergio Camello (12') overlaps
    3.5" on an axis whose top was 3.192.

    A lint that cries wolf on nearly every chart is worse than no lint,
    because the real findings stop being read.
    """
    try:
        pos = label.get_position()
        lo, hi = (axis.axes.get_ylim() if axis is axis.axes.yaxis
                  else axis.axes.get_xlim())
        v = pos[1] if axis is axis.axes.yaxis else pos[0]
        lo, hi = min(lo, hi), max(lo, hi)
        return lo <= v <= hi
    except Exception:
        return True   # cannot tell - keep it rather than hide a real overlap


def _texts(fig):
    """Every Text artist that actually got drawn, with its origin.

    The previous version scanned fig.texts ONLY. On a matplotlib chart that is
    almost nothing: titles, axis labels, annotations, tick labels and legend
    entries all live inside an Axes. A probe with seven deliberate failures
    inside the axes and one at figure level caught exactly one - the control.
    """
    inv = fig.transFigure.inverted()
    out = []

    def take(artist, origin):
        if artist is None or not _visible(artist):
            return
        s = artist.get_text().strip()
        if not s:
            return
        try:
            bb = artist.get_window_extent().transformed(inv)
        except (RuntimeError, ValueError):
            return
        if bb.width <= 0 or bb.height <= 0:
            return
        out.append((s, bb, artist.get_fontsize(), origin))

    for t in fig.texts:
        take(t, "figure")
    for leg in getattr(fig, "legends", []):
        for t in leg.get_texts():
            take(t, "figure legend")

    for i, ax in enumerate(fig.axes):
        tag = f"axes[{i}]"
        take(ax.title, f"{tag} title")
        for t in ax.texts:
            take(t, f"{tag} annotation")

        # An axes with axison=False paints NO axis furniture. matplotlib drops
        # the XAxis/YAxis artists at draw time, but the tick-label and
        # axis-label Text objects survive and still report get_visible()==True
        # - the same trap _tick_in_view was written for, one level up.
        #
        # Verified by pixel diff, not by reading matplotlib: with axis('off'),
        # adding xlabel/ylabel changes the rendered image not at all, while a
        # title or an ax.text() annotation does. So title and annotations are
        # taken above unconditionally and only the axis-owned furniture is
        # gated here.
        #
        # This matters because the CBS charts that draw onto a full-frame
        # invisible canvas - the whole Player Comparison family - reported
        # SIX phantom CLIPPED ticks per axis. On cell 5 that was 648 of 1302
        # findings, every one of them furniture that is never painted.
        if ax.axison:
            take(ax.xaxis.label, f"{tag} xlabel")
            take(ax.yaxis.label, f"{tag} ylabel")
            # Ticks outside the axes limits are kept by matplotlib and still
            # report visible, but are never drawn. Skip them or they collide
            # with everything placed near the edge of the plot.
            for t in ax.get_xticklabels():
                if _tick_in_view(t, ax.xaxis):
                    take(t, f"{tag} xtick")
            for t in ax.get_yticklabels():
                if _tick_in_view(t, ax.yaxis):
                    take(t, f"{tag} ytick")

        # A legend is NOT axis furniture - it is drawn on an axis-off axes like
        # any other artist, so it stays outside the gate above.
        leg = ax.get_legend()
        if leg is not None and _visible(leg):
            for t in leg.get_texts():
                take(t, f"{tag} legend")
    return out


def _overlap(a, b):
    dx = min(a.x1, b.x1) - max(a.x0, b.x0)
    dy = min(a.y1, b.y1) - max(a.y0, b.y0)
    return dx > 0 and dy > 0, max(0.0, dx) * max(0.0, dy), max(0.0, dx), max(0.0, dy)


def lint(fig, delivery="laptop", min_pt=None, tol=0.0015, clip_check=True,
         margin=0.0, patch_check=False):
    """Return a list of finding strings. Empty list = clean.

    delivery: "laptop" (16:9 parent) or "phone" (9:16 / 9:8 short). Sets the
              type floor. See DELIVERY - it is NOT inferred from figure shape.
    min_pt:   override the derived floor. Leave None in normal use.
    margin:   text closer than this to a canvas edge is a finding. CLIPPED
              only fires once text leaves the canvas, and a caption can bleed
              to 5px of the frame without ever crossing it - which is exactly
              what the pass map did, in the largest non-title type on the
              page, while linting clean (x0 = 0.0032). 0 disables.

              OFF by default, and that is a measurement rather than caution.
              add_cbs_footer positions by FRACTION (y=0.01), so the mark's
              real distance from the edge depends on figure height: 4.3pt on a
              16x9, 2.8pt on a 10x7, 0.7pt on a 6x4 - and this family ships
              figures from 4 to 16 inches tall. No single global floor clears
              the footer on the short ones without going under a genuine
              bleed on the tall ones, so any default either cries wolf on
              every chart or catches nothing. Pass a value per chart, once
              someone has looked at that chart's furniture. 0.006 suits 16x9.
    patch_check: also test figure text against PAINTED patches. Off by
              default because a chart may legitimately set a label on a
              shaded band; on for charts whose furniture must clear a drawn
              panel. See check 7 for why the axes box cannot answer this.
    """
    findings = []
    fig.canvas.draw()
    boxes = _texts(fig)
    if min_pt is None:
        min_pt = floor_pt(fig, delivery)

    # 1. text overlapping text, anywhere in the figure
    for i in range(len(boxes)):
        for j in range(i + 1, len(boxes)):
            (s1, b1, _, o1), (s2, b2, _, o2) = boxes[i], boxes[j]
            hit, area, dx, dy = _overlap(b1, b2)
            if not hit or area <= tol * tol:
                continue
            # A vertical graze is geometry, not a collision - same rule as
            # the JS lint. But the HORIZONTAL sliver rule is gone: for text
            # sharing a baseline, a small dx overlap is glyphs striking
            # through glyphs, and that rule suppressed THREE separate real
            # collisions (a foot caption over the marker key, stat labels
            # fusing at 16pt, a caption grazing the footer credit) while
            # each figure linted clean. Text boxes are wide and short, so
            # "small relative to the width" is not small.
            if dy < SLIVER * min(b1.height, b2.height):
                continue
            findings.append(
                f"OVERLAP: {s1[:30]!r} ({o1}) and {s2[:30]!r} ({o2}) "
                f"(area {area:.5f} of figure)")

    # 2. anything outside the canvas
    for s, b, _, o in boxes:
        if b.x0 < -0.002 or b.x1 > 1.002 or b.y0 < -0.002 or b.y1 > 1.002:
            findings.append(
                f"CLIPPED: {s[:30]!r} ({o}) extends to "
                f"x[{b.x0:.3f},{b.x1:.3f}] y[{b.y0:.3f},{b.y1:.3f}]")

    # 2b. text crowding a canvas edge without crossing it.
    if margin:
        for s_, b, _, o in boxes:
            near = min(b.x0, b.y0, 1.0 - b.x1, 1.0 - b.y1)
            if -0.002 <= near < margin:
                findings.append(
                    f"MARGIN: {s_[:30]!r} ({o}) comes within {near:.4f} of a "
                    f"canvas edge (floor {margin:.3f})")

    # 3. type below the legibility floor for the delivery context
    for s, _, pt, o in boxes:
        if pt < min_pt:
            eff = pt * DELIVERY[delivery][1] / (72.0 * fig.get_size_inches()[0])
            findings.append(
                f"TOO SMALL: {s[:30]!r} ({o}) at {pt:.1f}pt "
                f"= {eff:.1f}px delivered (floor {min_pt:.1f}pt / "
                f"{DELIVERY[delivery][0]}px)")

    # 4. FIGURE-level text landing on an axes region. Axes-owned text is
    #    excluded - a tick label is supposed to sit against its axes.
    #
    #    Axes with axison=False are skipped. get_position() is the LAYOUT box,
    #    not the painted content, and several CBS charts use a full-frame
    #    invisible axes purely as a drawing canvas - so its box is the entire
    #    figure and every title, subtitle and footer "lands on" it. That is
    #    219 of cell 5's 1302 findings and a 100% false-positive rate, which
    #    is the same mistake the DP lint made with a full-frame <svg>:
    #    measure what is PAINTED, not the box it was allotted.
    #
    #    The tradeoff is real and deliberate: figure text genuinely colliding
    #    with patches drawn on such a canvas is no longer caught here. Check 1
    #    still catches it whenever the thing collided with is TEXT, which is
    #    the case that has actually shipped.
    for i, ax in enumerate(fig.axes):
        if not ax.axison:
            continue
        ab = ax.get_position()
        for s, b, _, o in boxes:
            if o != "figure":
                continue
            hit, area, _, _ = _overlap(ab, b)
            if hit and area > tol * tol:
                findings.append(f"TEXT OVER AXES: {s[:30]!r} over axes[{i}]")

    # 5. plotted data running outside the axes limits.
    #    Off by default for pitch charts: mplsoccer draws pitch furniture as
    #    Line2D that is deliberately clipped, and inverts the x-axis.
    for ax in (fig.axes if clip_check else []):
        y0, y1 = sorted(ax.get_ylim())
        x0, x1 = sorted(ax.get_xlim())
        for ln in ax.get_lines():
            # Only DATA can be clipped by the data limits. Anything drawn in
            # another coordinate space is furniture, and its raw coordinates
            # mean something else entirely: an axvline carries y = 0..1 in
            # AXES fractions, and the xG race draws its goal markers on a
            # label row at y = 1.005 in the same space. Compared against a
            # data ylim of 0..0.903 all three look catastrophically clipped.
            #
            # This matters more than a merely noisy finding. "DATA CLIPPED" is
            # the exact wording of a REAL y-ceiling bug on record, so a false
            # one sends someone hunting a defect that is not there - or worse,
            # teaches them to skip the message that reports the true one.
            if ln.get_transform() is not ax.transData:
                continue
            xd, yd = ln.get_xdata(), ln.get_ydata()
            if len(yd) == 0:
                continue
            try:
                ymin, ymax = float(min(yd)), float(max(yd))
                xmin, xmax = float(min(xd)), float(max(xd))
            except (TypeError, ValueError):
                continue
            if ymax > y1 + 1e-9 or ymin < y0 - 1e-9:
                findings.append(
                    f"DATA CLIPPED: a series spans y[{ymin:.3f},{ymax:.3f}] "
                    f"but ylim is [{y0:.3f},{y1:.3f}]")
            if xmax > x1 + 1e-9 or xmin < x0 - 1e-9:
                findings.append(
                    f"DATA CLIPPED: a series spans x[{xmin:.1f},{xmax:.1f}] "
                    f"but xlim is [{x0:.1f},{x1:.1f}]")

    # 7. figure text landing on a PAINTED patch.
    #
    #    This is the case check 4 explicitly gives up on. A chart that draws
    #    its own panel onto an axison=False canvas has an axes box covering
    #    most of the figure, so check 4 would flag everything; but the PAINTED
    #    rectangle is a real, measurable thing, and furniture overlapping it is
    #    a real defect - the pass map's legend label sat 4.8px onto its pitch
    #    and its set-piece note 6.3px, invisible to every other check here.
    #
    #    Only opaque, non-background patches count: an alpha wash or a patch
    #    painted in the figure's own facecolor is not a surface text can sit
    #    "on top of" in any way a reader would notice.
    if patch_check:
        import matplotlib.colors as _mc
        bg = _mc.to_rgb(fig.get_facecolor())
        painted = []
        for src in [fig] + list(fig.axes):
            for pa in getattr(src, "patches", []):
                fc = pa.get_facecolor()
                if len(fc) == 4 and fc[3] < 0.9:
                    continue
                if max(abs(a - b) for a, b in zip(_mc.to_rgb(fc[:3]), bg)) < 0.02:
                    continue
                painted.append(pa.get_window_extent(fig.canvas.get_renderer())
                               .transformed(fig.transFigure.inverted()))
        for s_, b, _, o in boxes:
            if o != "figure":
                continue
            for pb in painted:
                hit, area, _, dy = _overlap(pb, b)
                if hit and area > tol * tol and dy > SLIVER * b.height:
                    findings.append(
                        f"TEXT ON PANEL: {s_[:30]!r} overlaps a painted patch "
                        f"by {dy:.4f} of figure height")
                    break

    # 6. plot area must not be squeezed out
    for i, ax in enumerate(fig.axes):
        p = ax.get_position()
        if p.height < 0.25:
            findings.append(
                f"PLOT TOO SHORT: axes[{i}] height {p.height:.3f} of figure")

    return findings


def report(fig, label="", **kw):
    f = lint(fig, **kw)
    tag = f"[{label}] " if label else ""
    if not f:
        print(f"{tag}lint: clean")
    else:
        print(f"{tag}lint: {len(f)} finding(s)")
        for x in f:
            print("   -", x)
    return f
