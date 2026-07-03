"""
Phase 8 - Generation des fichiers ASS (sous-titres karaoke mot par mot).

Module PUR (texte -> texte, aucun appel FFmpeg) : recalage des
timestamps, groupage des mots, conversion des styles YAML vers le
format [V4+ Styles], construction des evenements Dialogue.

Technique karaoke : un evenement Dialogue PAR MOT ACTIF — chaque
evenement affiche le groupe entier avec le mot actif enveloppe de tags
inline ({\\c&H...&}mot{\\r}, + \\fscx/\\fscy si highlight_scale).
C'est la technique des outils de clips viraux : controle total de la
couleur ET de la taille par mot. Le fallback non-karaoke produit un
evenement par groupe, sans tags.
"""

import re


# ---------------------------------------------------------------------------
# Conversions elementaires
# ---------------------------------------------------------------------------

def hex_to_ass_color(hex_color: str, transparency: float = 0.0) -> str:
    """
    Convertit une couleur hex RRGGBB vers le format ASS &HAABBGGRR
    (ordre inverse + canal alpha : 00 = opaque, FF = invisible).
    transparency : 0.0 = opaque, 1.0 = transparent.
    """
    hex_color = hex_color.strip().lstrip("#")
    if not re.fullmatch(r"[0-9a-fA-F]{6}", hex_color):
        raise ValueError(f"Couleur invalide (attendu RRGGBB) : {hex_color}")
    red, green, blue = hex_color[0:2], hex_color[2:4], hex_color[4:6]
    alpha = max(0, min(255, round(transparency * 255)))
    return f"&H{alpha:02X}{blue.upper()}{green.upper()}{red.upper()}"


def format_ass_time(seconds: float) -> str:
    """3725.567 -> '1:02:05.57' (format horodatage ASS, au centieme)."""
    seconds = max(0.0, seconds)
    total_centiseconds = round(seconds * 100)
    hours, remainder = divmod(total_centiseconds, 360000)
    minutes, remainder = divmod(remainder, 6000)
    secs, centiseconds = divmod(remainder, 100)
    return f"{hours}:{minutes:02d}:{secs:02d}.{centiseconds:02d}"


def escape_ass_text(text: str) -> str:
    """Echappe les accolades (sinon interpretees comme tags ASS)."""
    return text.replace("{", r"\{").replace("}", r"\}").replace("\n", r"\N")


# ---------------------------------------------------------------------------
# Recalage et groupage
# ---------------------------------------------------------------------------

def realign_words(words: list[dict], cut_start: float, cut_end: float) -> list[dict]:
    """
    Convertit les timestamps ABSOLUS (video source) en timestamps
    RELATIFS au clip : t_clip = t_source - cut_start.
    Un mot a 120.5 s avec cut_start=117.0 apparait a 3.5 s dans le clip.
    Les mots hors du clip sont exclus ; un mot a cheval sur une borne
    est garde et tronque (jamais supprime).
    """
    clip_duration = cut_end - cut_start
    realigned = []
    for word in words:
        if word["end"] <= cut_start or word["start"] >= cut_end:
            continue  # Entierement hors du clip
        start = max(0.0, word["start"] - cut_start)
        end = min(clip_duration, word["end"] - cut_start)
        if end - start < 0.02:
            continue  # Tronque a presque rien : inaffichable
        realigned.append({
            "word": word["word"],
            "start": round(start, 3),
            "end": round(end, 3),
        })
    return realigned


def group_words(words: list[dict], max_words_per_line: int,
                gap_threshold: float = 0.6) -> list[list[dict]]:
    """
    Decoupe une suite de mots (d'un meme segment) en groupes d'affichage :
    au plus max_words_per_line mots, et jamais a travers une pause
    superieure a gap_threshold (pendant les silences : aucun sous-titre).
    """
    groups: list[list[dict]] = []
    current: list[dict] = []
    for word in words:
        if current and (
            len(current) >= max_words_per_line
            or word["start"] - current[-1]["end"] > gap_threshold
        ):
            groups.append(current)
            current = []
        current.append(word)
    if current:
        groups.append(current)
    return groups


# ---------------------------------------------------------------------------
# Construction du fichier ASS
# ---------------------------------------------------------------------------

def build_style_line(style: dict, play_height: int = 1920) -> str:
    """
    Traduit un style de configs/subtitle_styles.yaml en ligne Style ASS.
    position_vertical (% depuis le haut) -> MarginV avec ancrage bas-centre
    (Alignment=2). MarginL/R 110 px : colonne sure hors du rail d'icones.
    """
    boxed = style.get("background") == "box"
    outline_color = hex_to_ass_color(style.get("outline_color", "000000"))
    background_color = hex_to_ass_color(
        style.get("background_color", "000000"),
        transparency=1.0 - style.get("background_opacity", 1.0),
    )
    margin_v = round(play_height * (1 - style.get("position_vertical", 68) / 100))

    fields = [
        "Default",
        style.get("font", "Arial"),
        style.get("font_size", 72),
        hex_to_ass_color(style.get("text_color", "FFFFFF")),   # PrimaryColour
        hex_to_ass_color(style.get("highlight_color", "FFD700")),  # SecondaryColour
        outline_color,
        background_color if boxed else outline_color,          # BackColour
        -1, 0, 0, 0,                                           # Bold, Italic, Underline, StrikeOut
        100, 100, 0, 0,                                        # ScaleX, ScaleY, Spacing, Angle
        3 if boxed else 1,                                     # BorderStyle (3 = fond boite)
        style.get("outline_width", 4),                         # Outline
        0,                                                     # Shadow
        2,                                                     # Alignment : bas-centre
        110, 110, margin_v,                                    # MarginL, MarginR, MarginV
        1,                                                     # Encoding
    ]
    return "Style: " + ",".join(str(f) for f in fields)


def _word_text(word: dict, style: dict) -> str:
    """Texte d'un mot, majuscules appliquees si le style le demande."""
    text = escape_ass_text(word["word"].strip())
    return text.upper() if style.get("uppercase") else text


def _dialogue(start: float, end: float, text: str, margin_v_override: int = 0) -> str:
    return (
        f"Dialogue: 0,{format_ass_time(start)},{format_ass_time(end)},"
        f"Default,,0,0,{margin_v_override},,{text}"
    )


def build_ass(groups: list[list[dict]], style: dict, karaoke: bool = True,
              play_res: tuple[int, int] = (1080, 1920),
              lead_in: float = 0.08, hold: float = 0.15) -> str:
    """
    Construit le contenu complet du fichier ASS.
    karaoke=True  : un evenement par mot actif (highlight dynamique) ;
    karaoke=False : un evenement par groupe (fallback statique).
    """
    width, height = play_res
    highlight = hex_to_ass_color(style.get("highlight_color", "FFD700"))
    scale = style.get("highlight_scale")
    scale_tag = (
        f"\\fscx{round(scale * 100)}\\fscy{round(scale * 100)}" if scale else ""
    )

    events = []
    for index, group in enumerate(groups):
        display_start = max(0.0, group[0]["start"] - lead_in)
        display_end = group[-1]["end"] + hold
        if index + 1 < len(groups):
            # Ne jamais chevaucher le groupe suivant
            next_start = max(0.0, groups[index + 1][0]["start"] - lead_in)
            display_end = min(display_end, max(display_start, next_start - 0.01))

        if not karaoke:
            text = " ".join(_word_text(w, style) for w in group)
            events.append(_dialogue(display_start, display_end, text))
            continue

        # Karaoke : un evenement par mot actif, couvrant [debut du mot ->
        # debut du mot suivant] (le highlight reste sur le mot pendant
        # les micro-pauses entre les mots)
        for word_index, word in enumerate(group):
            start = display_start if word_index == 0 else word["start"]
            end = (
                group[word_index + 1]["start"]
                if word_index + 1 < len(group) else display_end
            )
            if end <= start:
                continue
            parts = []
            for j, other in enumerate(group):
                text = _word_text(other, style)
                if j == word_index:
                    parts.append(f"{{\\c{highlight}&{scale_tag}}}{text}{{\\r}}")
                else:
                    parts.append(text)
            events.append(_dialogue(start, end, " ".join(parts)))

    header = f"""[Script Info]
; Genere par otherme_clipper (Phase 8)
ScriptType: v4.00+
PlayResX: {width}
PlayResY: {height}
WrapStyle: 2
ScaledBorderAndShadow: yes

[V4+ Styles]
Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, Alignment, MarginL, MarginR, MarginV, Encoding
{build_style_line(style, play_height=height)}

[Events]
Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text
"""
    return header + "\n".join(events) + "\n"
