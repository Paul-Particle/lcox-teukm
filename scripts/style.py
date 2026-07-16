"""FCA house plotting style for Plotly: a layout template and a colormap.

Provides the FCA color palette, the discrete `colorway`, the `fca_template`
layout, and the continuous `fca_colormap`.

Usage:
    from style import fca_template, fca_colormap, fca_colorway
    fig.update_layout(template=fca_template)              # house look
    fig.update_traces(marker=dict(colorscale=fca_colormap))  # continuous scale

Importing this module also registers the template as "fca" with Plotly, so
`fig.update_layout(template="fca")` works anywhere afterwards. `fca_logo()`
returns the brand monogram as an embeddable image (or None if the asset is
missing) for placing in a corner.

Note: the brand font is "Titillium Web". If it is not installed (e.g. during
static PNG export via kaleido) Plotly falls back to a default sans-serif; the
colors and layout are unaffected.
"""

import base64
import struct
from pathlib import Path

import plotly.graph_objects as go
import plotly.io as pio


# ---- FCA palette ---------------------------------------------------------
fca_blue = "#0A5680"
highlight_blue = "#0293D2"
light_blue = "#70D2F0"
sand_yellow = "#E2B681"
green = "#91C096"
magenta_red = "#D75674"
turquois = "#83D1DD"
blue_black = "#33434D"
very_dark_gray = "#525F6A"
dark_gray = "#71828F"
blue_gray = "#8CA5B7"
gray = "#B7C1C8"
light_blue_gray = "#BDCCD9"
light_gray = "#D6DBDF"

# Discrete trace cycle (used as the template's `colorway`).
fca_colorway = [
    fca_blue, dark_gray, light_blue, gray, highlight_blue,
    very_dark_gray, turquois, blue_black, light_blue_gray,
]

# Brand font family (the embedded weights are "Titillium Web" / "...SemiBold"). Use this for any
# per-element font (annotations, legends, tick labels) that doesn't inherit the template default.
BRAND_FONT = "Titillium Web"


# ---- Color helpers ---------------------------------------------------------

def lighten(hex_color: str, amount: float) -> str:
    """Blend a #rrggbb color toward white by `amount` in [0, 1] (0 = unchanged, 1 = white).
    Returns an `rgb(r, g, b)` string. Handy for tints/shades of the palette above."""
    r, g, b = (int(hex_color.lstrip("#")[i:i + 2], 16) for i in (0, 2, 4))
    r, g, b = (round(c + (255 - c) * amount) for c in (r, g, b))
    return f"rgb({r}, {g}, {b})"


def contrast_shades(n: int, max_lighten: float = 0.75) -> list[float]:
    """`n` `lighten` amounts (0 = full color … `max_lighten` = nearly white) ordered bottom-to-top
    for a stacked bar so adjacent segments contrast strongly. Even-length bars get the two darkest
    shades at both ends: take the levels darkest→lightest, move the darkest to the end, then zip
    the two halves — the 2nd-darkest leads, the darkest trails, every step jumps across the
    mid-range. For n=6 → [0.15, 0.60, 0.30, 0.75, 0.45, 0.00].

    An odd bar can't be dark at both ends, so it borrows one extra (unused) shade to build the even
    ordering, then drops the leading 2nd-darkest. The dark end (the darkest) stays at the top; the
    light end falls to the bottom, where it sits against the dark zero-line axis and still
    contrasts. For n=5 → [0.60, 0.30, 0.75, 0.45, 0.00]."""
    m = n if n % 2 == 0 else n + 1                           # work even; odd n borrows one shade
    levels = [max_lighten * i / (m - 1) for i in range(m)]   # index 0 darkest … m-1 lightest
    rotated = list(range(1, m)) + [0]                        # darkest (index 0) moved to the end
    cut = m // 2
    darker, lighter = rotated[:cut], rotated[cut:]
    order: list[int] = []
    for dark_idx, light_idx in zip(darker, lighter):
        order += [dark_idx, light_idx]
    shades = [levels[i] for i in order]
    return shades[1:] if n % 2 else shades                   # odd: drop the leading 2nd-darkest


# ---- Display toggles -------------------------------------------------------
# Set either to False to hide that element globally. When SHOW_DOT is False
# the title shifts flush to the left header edge (no indent for the dot gap).
SHOW_DOT = True    # leading brand dot to the left of the title
SHOW_LOGO = True   # FCA monogram in the bottom-right corner


# ---- Layout template -----------------------------------------------------
fca_template = go.layout.Template(
    layout=go.Layout(
        title=dict(
            xanchor="left",
            yanchor="top",
            y=0.93,    # pinned top so the leading dot can align to it
            x=0.0,     # left-aligned fallback; figures recompute x (see report.py)
            font=dict(family="Titillium Web SemiBold", size=22, color=blue_black),
        ),
        font=dict(family="Titillium Web", size=18, color=blue_black),
        margin=dict(l=60, r=40, t=96, b=64),
        xaxis=dict(
            title=dict(
                font=dict(family="Titillium Web", size=18, color=blue_black),
                standoff=10,
            ),
            ticklabelstandoff=10,
            showline=True,
            linewidth=2,
            linecolor=blue_black,
            # Log ticks are set per figure (data-range dependent) — see report.py.
        ),
        yaxis=dict(
            title_font=dict(family="Titillium Web", size=18, color=blue_black),
            ticklabelstandoff=10,
            gridcolor=light_gray,
            linewidth=2,
            linecolor=None,
            showline=False,
        ),
        colorway=fca_colorway,
        autosize=False,
        width=960,
        height=540,
        legend=dict(x=1, y=1, xanchor="right", yanchor="top",
                    bgcolor="rgba(255,255,255,0.65)", borderwidth=0,
                    font=dict(family="Titillium Web", size=12)),
    )
)

# Register so `template="fca"` resolves by name too.
pio.templates["fca"] = fca_template


# ---- Brand logo ----------------------------------------------------------
_LOGO_PATH = Path(__file__).parent / "assets" / "fca_logo.png"


def fca_logo():
    """The FCA monogram as a self-contained image dict, or None if absent.

    Returns ``{"source": <base64 PNG data URI>, "aspect": width / height}`` for
    ``fig.add_layout_image(...)``. fca_logo.png is rasterised from fca_logo.svg:
        magick -background none -density 600 scripts/assets/fca_logo.svg \\
            -resize x300 -depth 8 -strip scripts/assets/fca_logo.png
    """
    if not _LOGO_PATH.exists():
        return None
    data = _LOGO_PATH.read_bytes()
    width, height = struct.unpack(">II", data[16:24])  # PNG IHDR dimensions
    uri = "data:image/png;base64," + base64.b64encode(data).decode("ascii")
    return {"source": uri, "aspect": width / height}


# ---- Continuous colormap -------------------------------------------------
# RGB triples in 0-1, scaled to 0-255 for Plotly's rgb() strings below.
_CM_DATA = [
    [0.19336174, 0.26378673, 0.3025569],
    [0.19364304, 0.26610583, 0.30703146],
    [0.1939503, 0.26841819, 0.31146308],
    [0.19423889, 0.27072943, 0.31590992],
    [0.19449558, 0.2730411, 0.32038888],
    [0.19471714, 0.27535348, 0.32490372],
    [0.19491017, 0.27766572, 0.32944579],
    [0.19506328, 0.279979, 0.33402907],
    [0.19518186, 0.28229261, 0.33864631],
    [0.19526484, 0.28460656, 0.34329843],
    [0.19531079, 0.28692089, 0.34798671],
    [0.19531278, 0.28923619, 0.35271908],
    [0.19527956, 0.29155144, 0.35748422],
    [0.19520636, 0.29386701, 0.36228738],
    [0.19508798, 0.29618326, 0.36713415],
    [0.19493081, 0.29849945, 0.37201613],
    [0.19472688, 0.30081618, 0.37694211],
    [0.19447849, 0.3031331, 0.3819085],
    [0.19418938, 0.30544974, 0.38691015],
    [0.19384711, 0.30776702, 0.39196069],
    [0.19345767, 0.31008427, 0.39705215],
    [0.1930212, 0.31240133, 0.40218345],
    [0.19253425, 0.31471831, 0.40735751],
    [0.19199445, 0.31703523, 0.41257589],
    [0.19139719, 0.31935226, 0.41784256],
    [0.19074645, 0.32166891, 0.42315182],
    [0.19003849, 0.32398526, 0.4285065],
    [0.1892733, 0.32630113, 0.43390526],
    [0.18844592, 0.32861667, 0.439352],
    [0.18755791, 0.33093155, 0.44484351],
    [0.18660436, 0.33324588, 0.45038335],
    [0.18558353, 0.33555955, 0.45597159],
    [0.1844922, 0.33787252, 0.46160969],
    [0.18333085, 0.34018453, 0.46729518],
    [0.18210855, 0.34249884, 0.47298939],
    [0.18077026, 0.34482099, 0.47872449],
    [0.17930541, 0.34715201, 0.48450087],
    [0.17769591, 0.34949337, 0.49032426],
    [0.17593403, 0.35184605, 0.49618885],
    [0.17399912, 0.35421181, 0.50209846],
    [0.17188837, 0.35659279, 0.50803101],
    [0.16956348, 0.35899166, 0.51400137],
    [0.16700006, 0.36141038, 0.52000906],
    [0.16416455, 0.36385278, 0.52604771],
    [0.1610337, 0.36632444, 0.53208449],
    [0.15752577, 0.36882994, 0.53814882],
    [0.15360712, 0.37137813, 0.54418353],
    [0.14917845, 0.3739791, 0.55017893],
    [0.14413161, 0.37664676, 0.55609185],
    [0.13833039, 0.3794006, 0.56184617],
    [0.13164689, 0.38226569, 0.56729526],
    [0.12401748, 0.38526754, 0.57221162],
    [0.11578205, 0.38840983, 0.57621146],
    [0.1078785, 0.3916378, 0.57897519],
    [0.10137585, 0.39485695, 0.58053877],
    [0.09674017, 0.39800904, 0.58118626],
    [0.09379957, 0.4010781, 0.5812783],
    [0.09231343, 0.404072, 0.58099137],
    [0.09201055, 0.40700049, 0.58045697],
    [0.09264656, 0.40987383, 0.57975765],
    [0.09400814, 0.41269954, 0.57896166],
    [0.09596389, 0.41548377, 0.57809418],
    [0.09838625, 0.41823245, 0.57717698],
    [0.10118771, 0.42094994, 0.57621979],
    [0.10426088, 0.42363924, 0.57525581],
    [0.10755964, 0.42630393, 0.5742797],
    [0.11105303, 0.42894588, 0.57329017],
    [0.11467259, 0.43156794, 0.57230496],
    [0.11838823, 0.43417148, 0.57133048],
    [0.12217549, 0.43675883, 0.57036403],
    [0.12601263, 0.43933063, 0.56941473],
    [0.12988317, 0.44188893, 0.56847877],
    [0.13377245, 0.44443476, 0.56755975],
    [0.1376688, 0.44696937, 0.5666581],
    [0.14153679, 0.44949464, 0.56579001],
    [0.14533645, 0.45201521, 0.56495665],
    [0.14907306, 0.45453212, 0.56415069],
    [0.1527499, 0.45704568, 0.56337158],
    [0.15637008, 0.45955622, 0.56261832],
    [0.15993723, 0.46206392, 0.56189053],
    [0.16345349, 0.46456906, 0.56118778],
    [0.16692056, 0.46707211, 0.56050853],
    [0.17034054, 0.4695733, 0.55985248],
    [0.17371552, 0.47207293, 0.5592185],
    [0.17704732, 0.47457122, 0.55860619],
    [0.18033785, 0.47706849, 0.55801408],
    [0.1835887, 0.47956494, 0.55744177],
    [0.18680139, 0.48206077, 0.55688878],
    [0.18997745, 0.4845562, 0.55635427],
    [0.19311831, 0.48705145, 0.5558374],
    [0.19622544, 0.48954679, 0.55533668],
    [0.1993, 0.49204229, 0.55485239],
    [0.20234332, 0.49453818, 0.55438345],
    [0.2053567, 0.49703468, 0.55392845],
    [0.20834131, 0.4995319, 0.55348698],
    [0.21129823, 0.50202994, 0.55305894],
    [0.21423037, 0.5045288, 0.55264248],
    [0.21713768, 0.50702865, 0.55223802],
    [0.22002113, 0.50952974, 0.55184365],
    [0.22288185, 0.51203218, 0.55145867],
    [0.22572095, 0.51453603, 0.55108258],
    [0.22853965, 0.51704145, 0.55071371],
    [0.23133901, 0.51954843, 0.5503522],
    [0.23412028, 0.52205714, 0.54999604],
    [0.23688457, 0.52456754, 0.54964538],
    [0.23963426, 0.52707952, 0.54929904],
    [0.24237281, 0.5295926, 0.54895763],
    [0.24509837, 0.53210743, 0.54861884],
    [0.24781224, 0.53462407, 0.54828107],
    [0.25051565, 0.53714245, 0.54794429],
    [0.25320995, 0.53966259, 0.54760699],
    [0.25590104, 0.54218354, 0.5472701],
    [0.25858787, 0.5447057, 0.54693227],
    [0.26127012, 0.54722939, 0.54659136],
    [0.26394926, 0.54975456, 0.54624622],
    [0.26663083, 0.55228029, 0.54589767],
    [0.26931658, 0.55480638, 0.54554523],
    [0.27200426, 0.55733352, 0.54518609],
    [0.27469584, 0.55986155, 0.54481921],
    [0.27740194, 0.56238835, 0.54444808],
    [0.28011546, 0.56491569, 0.54406727],
    [0.28283875, 0.56744322, 0.54367622],
    [0.28558354, 0.56996861, 0.54327876],
    [0.2883417, 0.5724938, 0.54286894],
    [0.29112033, 0.5750174, 0.54244855],
    [0.29392364, 0.57753865, 0.5420178],
    [0.29674753, 0.58005861, 0.54157256],
    [0.29960634, 0.58257432, 0.54111751],
    [0.30248959, 0.58508816, 0.5406462],
    [0.30541324, 0.58759671, 0.54016467],
    [0.30838386, 0.59009942, 0.53966529],
    [0.31144311, 0.59258828, 0.53915271],
    [0.31457214, 0.59506674, 0.53862345],
    [0.31779406, 0.59753002, 0.53808181],
    [0.32109221, 0.5999811, 0.53752541],
    [0.32448098, 0.60241683, 0.53695686],
    [0.32795593, 0.60483771, 0.53637638],
    [0.33151607, 0.60724362, 0.53578405],
    [0.33517373, 0.60963158, 0.53518369],
    [0.33891568, 0.6120039, 0.53457346],
    [0.3427525, 0.61435794, 0.53395634],
    [0.34668295, 0.6166934, 0.53333407],
    [0.35070028, 0.61901123, 0.53270641],
    [0.35481462, 0.6213087, 0.53207725],
    [0.35902089, 0.62358633, 0.5314474],
    [0.36331371, 0.62584469, 0.53081753],
    [0.36769913, 0.62808182, 0.53019144],
    [0.37217552, 0.63029744, 0.52957098],
    [0.37673515, 0.63249267, 0.52895645],
    [0.38137776, 0.63466691, 0.52835043],
    [0.38611007, 0.6368179, 0.52775776],
    [0.39091931, 0.63894796, 0.52717741],
    [0.39580297, 0.64105706, 0.52661143],
    [0.40076431, 0.64314374, 0.52606386],
    [0.40579878, 0.64520842, 0.52553636],
    [0.41089833, 0.64725247, 0.5250292],
    [0.41605959, 0.64927614, 0.52454417],
    [0.42128914, 0.65127714, 0.5240867],
    [0.42657345, 0.65325835, 0.52365484],
    [0.43190845, 0.65522031, 0.52324991],
    [0.43729569, 0.65716209, 0.52287535],
    [0.44273142, 0.65908422, 0.52253229],
    [0.44820671, 0.66098868, 0.5222196],
    [0.45372083, 0.66287526, 0.52193934],
    [0.45927507, 0.66474323, 0.5216946],
    [0.46485819, 0.66659541, 0.52148232],
    [0.47047027, 0.66843154, 0.5213042],
    [0.47611267, 0.67025093, 0.52116289],
    [0.48177435, 0.67205663, 0.52105432],
    [0.48745628, 0.67384829, 0.52097894],
    [0.49315452, 0.67562697, 0.52093603],
    [0.49886304, 0.67739448, 0.52092277],
    [0.5045901, 0.6791481, 0.52094544],
    [0.51032434, 0.6808914, 0.52099791],
    [0.51607009, 0.68262299, 0.52108309],
    [0.52182449, 0.6843437, 0.52120077],
    [0.52758474, 0.68605457, 0.52134836],
    [0.53335469, 0.68775423, 0.52153001],
    [0.53912625, 0.68944556, 0.52173917],
    [0.54490782, 0.69112564, 0.52198308],
    [0.55068869, 0.69279831, 0.52225316],
    [0.55647841, 0.69446031, 0.52255638],
    [0.56226607, 0.69611551, 0.52288524],
    [0.56806153, 0.69776059, 0.5232461],
    [0.57385501, 0.69939918, 0.52363077],
    [0.57965521, 0.70102815, 0.52404692],
    [0.58545369, 0.7026508, 0.52448627],
    [0.59125739, 0.7042647, 0.52495385],
    [0.59706151, 0.70587175, 0.5254452],
    [0.60286782, 0.70747143, 0.52596129],
    [0.60867759, 0.70906337, 0.52650253],
    [0.6144868, 0.71064932, 0.52706444],
    [0.62030288, 0.71222648, 0.52765273],
    [0.62611978, 0.71379741, 0.52826106],
    [0.63193919, 0.71536155, 0.5288904],
    [0.63776486, 0.71691754, 0.52954313],
    [0.64359244, 0.71846728, 0.5302144],
    [0.6494244, 0.72000988, 0.53090591],
    [0.65526331, 0.72154441, 0.53161893],
    [0.66110569, 0.72307243, 0.53234969],
    [0.66695291, 0.72459349, 0.53309853],
    [0.67280945, 0.7261058, 0.53386897],
    [0.67867109, 0.72761122, 0.53465664],
    [0.68453833, 0.72910962, 0.53546156],
    [0.690414, 0.73059989, 0.5362857],
    [0.69629887, 0.73208176, 0.53712903],
    [0.70219121, 0.73355607, 0.53798943],
    [0.70809123, 0.73502275, 0.53886718],
    [0.71400019, 0.73648127, 0.5397635],
    [0.71992042, 0.73793064, 0.54068002],
    [0.72585007, 0.73937178, 0.54161418],
    [0.73178821, 0.74080499, 0.54256686],
    [0.7377363, 0.74222974, 0.54353793],
    [0.74369493, 0.74364577, 0.54452813],
    [0.74967672, 0.74504881, 0.54552801],
    [0.7556707, 0.74644644, 0.54649358],
    [0.7616816, 0.74783692, 0.54742283],
    [0.76771175, 0.74921935, 0.54831463],
    [0.77375806, 0.75059494, 0.5491687],
    [0.77981703, 0.75196514, 0.54998449],
    [0.78589684, 0.75332665, 0.55076069],
    [0.79199845, 0.75467909, 0.55149619],
    [0.79810993, 0.75602746, 0.55218964],
    [0.80424475, 0.75736624, 0.55283912],
    [0.81039833, 0.75869739, 0.55344303],
    [0.81656628, 0.7600229, 0.5539992],
    [0.82275821, 0.76133883, 0.5545044],
    [0.82896261, 0.76265033, 0.55495538],
    [0.83518836, 0.76395367, 0.5553498],
    [0.84143097, 0.76525122, 0.55568118],
    [0.84768938, 0.76654365, 0.55594644],
    [0.85396784, 0.76782946, 0.55613943],
    [0.86025979, 0.76911207, 0.5562531],
    [0.86656526, 0.77039202, 0.55627941],
    [0.87288638, 0.77166902, 0.55620909],
    [0.87921949, 0.77294545, 0.55603219],
    [0.88556221, 0.77422333, 0.55573589],
    [0.89191146, 0.77550521, 0.55530557],
    [0.8982652, 0.77679341, 0.55472277],
    [0.90460776, 0.77809693, 0.55396664],
    [0.91093913, 0.77941765, 0.55301401],
    [0.91723861, 0.78076783, 0.55183511],
    [0.923489, 0.78215831, 0.55039928],
    [0.92965529, 0.78360913, 0.54867087],
    [0.93568735, 0.78514753, 0.54662123],
    [0.94152001, 0.78680765, 0.54423419],
    [0.9470492, 0.78864076, 0.54152929],
    [0.95222783, 0.79066624, 0.53858573],
    [0.95735941, 0.79272319, 0.53556443],
    [0.96251067, 0.79477643, 0.53249434],
    [0.96768256, 0.79682563, 0.52937249],
    [0.97287471, 0.79887088, 0.52619923],
    [0.97808875, 0.80091159, 0.5229697],
    [0.98332536, 0.80294748, 0.51968139],
    [0.98858843, 0.80497722, 0.51632348],
    [0.99391604, 0.8069879, 0.51279565],
]

# Plotly continuous colorscale: list of [position 0-1, "rgb(r,g,b)"] pairs.
_n = len(_CM_DATA)
fca_colormap = [
    [i / (_n - 1), f"rgb({round(r * 255)}, {round(g * 255)}, {round(b * 255)})"]
    for i, (r, g, b) in enumerate(_CM_DATA)
]

# ---- Colormap shades note --------------------------------------------------
# To sample N shades from one color family (e.g. multiple battery scenarios in
# the blue–teal range) use:
#
#   import plotly.colors as pc
#   shades = pc.sample_colorscale(fca_colormap, [i / (N - 1) for i in range(N)])
#
# The colormap spans blue-grey → teal → sand so it does NOT reproduce the
# exact named palette colors, but approximates them well at the dark-blue and
# sand ends. For FCA green or magenta traces use the named colors above.


# ---- Titillium Web font embedding -----------------------------------------
#
# HTML output uses whatever fonts the browser finds; kaleido PNG/SVG uses the
# system font library. To guarantee the brand font in self-contained HTML:
#
# Option A — online (Google Fonts CDN, zero setup, requires internet):
#   html = fig.to_html(include_plotlyjs=True)
#   html = inject_titillium_font(html)
#   Path("output.html").write_text(html, encoding="utf-8")
#
# Option B — fully offline (embed base64 .woff2, no network needed):
#   1. Download Titillium Web 400 + 600 .woff2 from fonts.google.com.
#   2. Base64-encode: base64 -i TitilliumWeb-Regular.woff2 | tr -d '\n'
#   3. Paste the output string into _TW_REGULAR_B64 / _TW_SEMIBOLD_B64 below.
#      inject_titillium_font() will use the blobs when they are present.
#
# For kaleido PNG/SVG: install the font on the OS so kaleido can find it.
#   macOS: open the .ttf/.otf file and click "Install Font".
#   Linux: copy .ttf to ~/.local/share/fonts/ then run `fc-cache -fv`.

_TW_GOOGLE_FONTS_URL = (
    "https://fonts.googleapis.com/css2?"
    "family=Titillium+Web:ital,wght@0,400;0,600;1,400&display=swap"
)
# Paste base64-encoded .woff2 strings here for fully-offline HTML embedding.
_TW_REGULAR_B64: str = ""
_TW_SEMIBOLD_B64: str = ""


def inject_titillium_font(html_str: str) -> str:
    """Inject Titillium Web into the <head> of a Plotly HTML string.

    Uses embedded base64 .woff2 blobs when _TW_REGULAR_B64 / _TW_SEMIBOLD_B64
    are filled in; otherwise falls back to a Google Fonts CDN <link>.

    Usage::

        html = fig.to_html(include_plotlyjs=True)
        html = inject_titillium_font(html)
        Path("output.html").write_text(html, encoding="utf-8")
    """
    if _TW_REGULAR_B64 and _TW_SEMIBOLD_B64:
        css = (
            "<style>"
            "@font-face{font-family:'Titillium Web';font-weight:400;"
            f"src:url('data:font/woff2;base64,{_TW_REGULAR_B64}') format('woff2');}}"
            "@font-face{font-family:'Titillium Web';font-weight:600;"
            f"src:url('data:font/woff2;base64,{_TW_SEMIBOLD_B64}') format('woff2');}}"
            "</style>"
        )
    else:
        css = (
            '<link rel="preconnect" href="https://fonts.googleapis.com">'
            '<link rel="preconnect" href="https://fonts.gstatic.com" crossorigin>'
            f'<link href="{_TW_GOOGLE_FONTS_URL}" rel="stylesheet">'
        )
    return html_str.replace("<head>", f"<head>{css}", 1)


def save_figure(fig, out_dir, stem: str, *, scale: int = 2) -> list[str]:
    """Write `fig` the house way: a self-contained, font-injected HTML plus a retina (scale=2)
    static PNG. PNG export is skipped with a note if kaleido is unavailable. Returns saved paths."""
    out = Path(out_dir)
    out.mkdir(parents=True, exist_ok=True)
    saved = []
    html_path = out / f"{stem}.html"
    html_path.write_text(inject_titillium_font(fig.to_html(include_plotlyjs=True)), encoding="utf-8")
    saved.append(str(html_path))
    png_path = out / f"{stem}.png"
    try:
        fig.write_image(str(png_path), scale=scale)
        saved.append(str(png_path))
    except Exception as e:
        print(f"PNG export skipped ({stem}):", e)
    return saved


# ---- Annotation-label recipe (replaces the built-in Plotly legend) ---------
#
# Each label is a solid rectangle of the trace color with white text — more
# compact and visually consistent with the FCA palette than the default legend.
#
# Typical usage (two traces, labels stacked in the upper-right corner):
#
#   fig.update_layout(showlegend=False)
#   add_trace_label(fig, "fossil",           color=blue_black,  x=0.97, y=0.92)
#   add_trace_label(fig, "battery-electric", color=fca_blue,    x=0.97, y=0.81)
#
# x/y are paper coords (0–1). xanchor="right" pins the right edge of the box.
# Vertical spacing: ~0.11 per label at font_size=14 with fig_height≈520.
# Adjust y-gap proportionally when figure height changes.
#
# This is the DEFAULT/preferred style, but it only reads well for a few traces
# (~2–4): the stacked rectangles crowd and overlap the curves beyond that. For
# many-trace plots fall back to a real (translucent) Plotly legend instead —
# see plot_lcot_vs_dmax / plot_speed_vs_dmax in report.py (7 cases each).

def add_trace_label(fig, text: str, color: str,
                    x: float = 0.97, y: float = 0.90,
                    xref: str = "paper", yref: str = "paper",
                    font_size: int = 14) -> None:
    """Add a solid-rectangle annotation label (preferred legend replacement)."""
    fig.add_annotation(
        text=f"  {text}  ",
        x=x, y=y, xref=xref, yref=yref,
        showarrow=False, xanchor="right", yanchor="middle",
        bgcolor=color, borderpad=6, bordercolor=color,
        font=dict(family="Titillium Web", size=font_size, color="white"),
    )


# ---- Header / brand helpers ------------------------------------------------

def header_geometry(fig_width: int, fig_height: int,
                    margin_l: int = None, margin_r: int = None,
                    margin_t: int = None) -> dict:
    """Pixel/fraction coordinates shared by dot, title, subtitle, and footnote.

    Returns a dict with:
        header_left_px  left edge of all header items (px from figure left)
        header_x_shift  shift from paper x=0 to header_left (px, usually negative)
        dot_d           leading-dot diameter (px)
        title_x         paper-fraction x for the title (clears dot when SHOW_DOT)
        dot_up          yshift (px, positive = up) to center dot on the cap-line
    """
    if margin_l is None:
        margin_l = fca_template.layout.margin.l
    if margin_r is None:
        margin_r = fca_template.layout.margin.r
    if margin_t is None:
        margin_t = fca_template.layout.margin.t
    title_size = fca_template.layout.title.font.size
    header_left_px = margin_l - 30
    header_x_shift = header_left_px - margin_l
    dot_d = 0.3125 * title_size
    if SHOW_DOT:
        title_x = (header_left_px + dot_d + title_size / 4) / fig_width
    else:
        title_x = header_left_px / fig_width
    cap_mid_px = (1 - fca_template.layout.title.y) * fig_height + 0.42 * title_size
    dot_up = margin_t - cap_mid_px
    return dict(
        header_left_px=header_left_px,
        header_x_shift=header_x_shift,
        dot_d=dot_d,
        title_x=title_x,
        dot_up=dot_up,
    )


def apply_dot(fig, geom: dict) -> None:
    """Draw the leading brand dot if SHOW_DOT is True."""
    if not SHOW_DOT:
        return
    d = geom["dot_d"]
    fig.add_shape(
        type="circle", xref="paper", yref="paper",
        xsizemode="pixel", ysizemode="pixel", xanchor=0, yanchor=1,
        x0=geom["header_x_shift"], x1=geom["header_x_shift"] + d,
        y0=geom["dot_up"] - d / 2, y1=geom["dot_up"] + d / 2,
        fillcolor=highlight_blue, line_width=0, layer="above",
    )


def apply_logo(fig, fig_width: int, fig_height: int,
               margin_l: int, margin_r: int,
               margin_t: int, margin_b: int) -> None:
    """Place the brand monogram bottom-right if SHOW_LOGO is True."""
    if not SHOW_LOGO:
        return
    logo = fca_logo()
    if not logo:
        return
    plot_w_px = fig_width - margin_l - margin_r
    plot_h_px = fig_height - margin_t - margin_b
    logo_h_px = 22
    fig.add_layout_image(
        source=logo["source"], xref="paper", yref="paper",
        xanchor="right", yanchor="bottom",
        x=1, y=-(margin_b - 28) / plot_h_px,
        sizex=logo_h_px * logo["aspect"] / plot_w_px,
        sizey=logo_h_px / plot_h_px, sizing="contain", layer="above",
    )


def apply_header(fig, *, title: str, subtitle: str,
                 fig_width: int, fig_height: int, margin_b: int,
                 margin_l: int = None, margin_r: int = None) -> dict:
    """Apply the full FCA header chrome to `fig`: dot-aligned title, the subtitle line beneath it,
    the leading brand dot, and the bottom-right monogram. Also pins the figure size and margins
    (the dot/logo geometry depends on them). `margin_l`/`margin_r` default to the template but can
    be overridden (e.g. wider left for horizontal-bar labels) — the header geometry follows so the
    dot stays aligned. Set the template and everything else on `fig` first, then call this."""
    margin_l = fca_template.layout.margin.l if margin_l is None else margin_l
    margin_r = fca_template.layout.margin.r if margin_r is None else margin_r
    margin_t = fca_template.layout.margin.t
    geom = header_geometry(fig_width, fig_height, margin_l, margin_r, margin_t)
    fig.update_layout(title=dict(text=title, x=geom["title_x"]),
                      width=fig_width, height=fig_height,
                      margin=dict(b=margin_b, l=margin_l, r=margin_r))
    fig.add_annotation(
        text=subtitle, xref="paper", yref="paper",
        x=0, xanchor="left", xshift=geom["header_x_shift"],
        y=1, yanchor="bottom", yshift=12, showarrow=False,
        font=dict(family=BRAND_FONT, size=18, color=blue_black),
    )
    apply_dot(fig, geom)
    apply_logo(fig, fig_width, fig_height, margin_l, margin_r, margin_t, margin_b)
    return geom
