import argparse
from bisect import bisect_right
import json
import shutil
import subprocess
from collections import defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Any
import zipfile

from PIL import Image, ImageDraw, ImageFont


BASMALLAH_TEXT = "بِسْمِ اللّٰهِ الرَّحْمٰنِ الرَّحِيْمِ"
COVER_TEXT_DEFAULT = "الْقُرْآن"
DEFAULT_OUTPUT_WIDTH = 1240
DEFAULT_OUTPUT_HEIGHT = 2100
DEFAULT_IMAGES_OUT_DIR = Path("outputs/quran_16line_hd_images")
DEFAULT_COORDS_OUT_DIR = Path("outputs/quran_16line_hd_coords")
PARA_PAGE_NUMBERS_PATH = Path("quran-indopak/assets/para_page_numbers.json")
SURAH_NAMES_PATH = Path("quran-indopak/assets/surah_names.json")
DEFAULT_RESOLUTION_PRESETS: list[tuple[str, int, int]] = [
    ("2480x4200 (HD)", 2480, 4200),
    ("1240x2100 (Standard)", 1240, 2100),
    ("1860x3150 (Balanced)", 1860, 3150),
]


@dataclass
class PreparedWord:
    text: str
    ayah_number: int


@dataclass
class PreparedLine:
    page_number: int
    line_number: int
    line_type: str
    is_centered: bool
    surah_number: int
    text: str
    words: list[PreparedWord]


@dataclass
class AyahBox:
    ref_key: str
    surah_number: int
    ayah_number: int
    line_number: int
    x: float
    y: float
    width: float
    height: float


def parse_args():
    parser = argparse.ArgumentParser(
        description="Render high-quality IndoPak 16-line Quran pages as WebP snapshots."
    )
    parser.add_argument("--start-page", type=int, default=1, help="Starting page number (default: 1)")
    parser.add_argument("--page-count", type=int, default=549, help="Number of pages to render (default: 549)")
    parser.add_argument(
        "--width",
        type=int,
        default=DEFAULT_OUTPUT_WIDTH,
        help=f"Output image width in pixels (default: {DEFAULT_OUTPUT_WIDTH})",
    )
    parser.add_argument(
        "--height",
        type=int,
        default=DEFAULT_OUTPUT_HEIGHT,
        help=f"Output image height in pixels (default: {DEFAULT_OUTPUT_HEIGHT})",
    )
    # DPI is not used for WebP, but keep for compatibility
    parser.add_argument("--dpi", type=int, default=300, help="(Unused for WebP) PNG DPI metadata (default: 300)")
    parser.add_argument(
        "--webp-quality",
        type=int,
        default=90,
        help="WebP quality (default: 90)",
    )
    parser.add_argument(
        "--webp-lossless",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Use lossless WebP for maximum glyph clarity (default: true)",
    )
    parser.add_argument(
        "--stroke-width-ratio",
        type=float,
        default=0.00065,
        help="Stroke width ratio for ayah text (default: 0.00065)",
    )
    parser.add_argument(
        "--header-stroke-width-ratio",
        type=float,
        default=0.00085,
        help="Stroke width ratio for surah/basmallah/cover text (default: 0.00085)",
    )
    parser.add_argument(
        "--font-path",
        type=Path,
        default=Path("quran-indopak/assets/fonts/IndoPakNastaleeq.ttf"),
        help="Path to IndoPakNastaleeq.ttf",
    )
    parser.add_argument(
        "--layout-path",
        type=Path,
        default=Path("quran-indopak/assets/quran-16line-layout.json"),
        help="Path to quran-16line-layout.json",
    )
    parser.add_argument(
        "--word-by-word-path",
        type=Path,
        default=Path("quran-indopak/quran-indopak-nastaleeq-word-by-word.json"),
        help="Path to quran-indopak-nastaleeq-word-by-word.json",
    )
    parser.add_argument(
        "--surah-names-path",
        type=Path,
        default=SURAH_NAMES_PATH,
        help="Path to surah_names.json",
    )
    parser.add_argument(
        "--out-dir",
        type=Path,
        default=DEFAULT_IMAGES_OUT_DIR,
        help="Directory where rendered pages are saved",
    )
    parser.add_argument(
        "--coords-out-dir",
        type=Path,
        default=DEFAULT_COORDS_OUT_DIR,
        help="Directory where per-page ayah coordinate JSON is saved",
    )
    parser.add_argument(
        "--add-cover-page",
        action="store_true",
        default=True,
        help="Add a white title cover as output page_001 and shift Quran pages by +1",
    )
    parser.add_argument(
        "--cover-text",
        type=str,
        default=COVER_TEXT_DEFAULT,
        help="Arabic title text for the generated cover page",
    )
    parser.add_argument(
        "--panel-vertical-padding-ratio",
        type=float,
        default=0.001,
        help="Top/bottom outer panel padding ratio of page height (default: 0.001)",
    )
    parser.add_argument(
        "--text-vertical-padding-ratio",
        type=float,
        default=0.001,
        help="Top/bottom text-area padding ratio inside inner border (default: 0.001)",
    )
    parser.add_argument(
        "--max-ayah-font-size-ratio",
        type=float,
        default=0.74,
        help="Maximum ayah font size as a ratio of line slot height (default: 0.74)",
    )
    parser.add_argument(
        "--max-centered-ayah-font-size-ratio",
        type=float,
        default=0.55,
        help="Maximum centered ayah font size as a ratio of line slot height (default: 0.62)",
    )
    parser.add_argument(
        "--max-basmallah-font-size-ratio",
        type=float,
        default=0.60,
        help="Maximum basmallah font size as a ratio of line slot height (default: 0.60)",
    )
    parser.add_argument(
        "--interactive",
        action=argparse.BooleanOptionalAction,
        default=True,
        help="Ask interactively for resolution and archive format (default: true)",
    )
    parser.add_argument(
        "--archive-format",
        choices=["none", "zip", "7z"],
        default="7z",
        help="Archive format after export in non-interactive mode (default: 7z)",
    )
    return parser.parse_args()


def prompt_resolution_presets() -> list[tuple[int, int]]:
    print("\nSelect resolution to export:")
    print("  1. 2480x4200 (HD)")
    print("  2. 1240x2100 (Standard)")
    print("  3. 1860x3150 (Balanced)")
    print("  4. All")

    while True:
        choice = input("Enter choice [default: 2]: ").strip() or "2"
        if choice == "1":
            return [(2480, 4200)]
        if choice == "2":
            return [(1240, 2100)]
        if choice == "3":
            return [(1860, 3150)]
        if choice == "4":
            return [(2480, 4200), (1240, 2100), (1860, 3150)]
        print("Invalid choice. Please choose 1, 2, 3, or 4.")


def prompt_archive_format() -> str:
    print("\nSelect archive format:")
    print("  1. zip")
    print("  2. 7z (default)")
    print("  3. none")

    while True:
        choice = input("Enter choice [default: 2]: ").strip() or "2"
        if choice == "1":
            return "zip"
        if choice == "2":
            return "7z"
        if choice == "3":
            return "none"
        print("Invalid choice. Please choose 1, 2, or 3.")


def create_zip_archive(source_dir: Path) -> Path:
    archive_path = source_dir.parent / f"{source_dir.name}.zip"
    with zipfile.ZipFile(archive_path, mode="w", compression=zipfile.ZIP_DEFLATED, compresslevel=9) as zip_handle:
        for path in sorted(source_dir.rglob("*")):
            if path.is_file():
                arcname = path.relative_to(source_dir.parent)
                zip_handle.write(path, arcname=str(arcname))
    return archive_path


def create_7z_archive(source_dir: Path) -> Path:
    archive_path = source_dir.parent / f"{source_dir.name}.7z"

    try:
        import py7zr  # type: ignore

        with py7zr.SevenZipFile(archive_path, mode="w") as seven_zip:
            seven_zip.writeall(str(source_dir), arcname=source_dir.name)
        return archive_path
    except ImportError:
        pass

    seven_zip_binary = shutil.which("7z") or shutil.which("7za")
    if not seven_zip_binary:
        raise RuntimeError(
            "7z archive requested, but neither py7zr nor a 7z binary is available. "
            "Install py7zr (pip install py7zr) or install p7zip."
        )

    subprocess.run(
        [seven_zip_binary, "a", "-t7z", "-mx=9", str(archive_path), source_dir.name],
        cwd=source_dir.parent,
        check=True,
    )
    return archive_path


def create_archive(source_dir: Path, archive_format: str) -> Path | None:
    if archive_format == "none":
        return None
    if archive_format == "zip":
        return create_zip_archive(source_dir)
    if archive_format == "7z":
        return create_7z_archive(source_dir)
    raise ValueError(f"Unsupported archive format: {archive_format}")


def load_json(path: Path):
    try:
        with path.open("r", encoding="utf-8") as handle:
            return json.load(handle)
    except json.JSONDecodeError as error:
        raise ValueError(f"Invalid JSON in {path}: {error}") from error


def strip_hash_comments(text: str) -> str:
    lines: list[str] = []
    for raw_line in text.splitlines():
        line = raw_line
        in_string = False
        escaped = False
        for index, char in enumerate(raw_line):
            if escaped:
                escaped = False
                continue
            if char == "\\":
                escaped = True
                continue
            if char == '"':
                in_string = not in_string
                continue
            if char == "#" and not in_string:
                line = raw_line[:index]
                break
        lines.append(line)
    return "\n".join(lines)


def remove_trailing_commas(text: str) -> str:
    result: list[str] = []
    in_string = False
    escaped = False
    index = 0
    while index < len(text):
        char = text[index]
        if in_string:
            result.append(char)
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                in_string = False
            index += 1
            continue

        if char == '"':
            in_string = True
            result.append(char)
            index += 1
            continue

        if char == ",":
            look_ahead = index + 1
            while look_ahead < len(text) and text[look_ahead] in {" ", "\t", "\n", "\r"}:
                look_ahead += 1
            if look_ahead < len(text) and text[look_ahead] in {"]", "}"}:
                index += 1
                continue

        result.append(char)
        index += 1

    return "".join(result)


def load_para_page_starts(path: Path = PARA_PAGE_NUMBERS_PATH) -> list[int]:
    if not path.exists():
        raise FileNotFoundError(f"Para page numbers file not found: {path}")

    values = load_json(path)
    if not isinstance(values, list):
        raise ValueError(f"Invalid para page numbers format in {path}. Expected a JSON list.")

    starts: list[int] = []
    for value in values:
        number = parse_number(value)
        if number is None:
            raise ValueError(f"Invalid para start page value in {path}: {value!r}")
        starts.append(number)

    if len(starts) != 30:
        raise ValueError(f"Expected 30 para start pages in {path}, found {len(starts)}")

    if starts != sorted(starts):
        raise ValueError(f"Para start pages in {path} must be sorted ascending")

    return starts


def get_para_folder_name(page_number: int, para_page_starts: list[int]) -> str:
    para_index = bisect_right(para_page_starts, page_number) - 1
    if para_index < 0:
        raise ValueError(f"Page {page_number} is below the first para start page")
    if para_index >= len(para_page_starts):
        raise ValueError(f"Page {page_number} does not map to a valid para")
    return f"para_{para_index + 1:02d}"


def resolve_bundle_output_root(out_dir: Path, coords_out_dir: Path) -> Path:
    if out_dir == DEFAULT_IMAGES_OUT_DIR and coords_out_dir == DEFAULT_COORDS_OUT_DIR:
        return Path("outputs/quran_by_para")
    return out_dir


def load_surah_names(path: Path = SURAH_NAMES_PATH) -> dict[int, str]:
    if not path.exists():
        raise FileNotFoundError(
            f"Surah names file not found: {path}. "
            "Create this file or pass --surah-names-path."
        )

    raw_text = path.read_text(encoding="utf-8")
    cleaned_text = remove_trailing_commas(strip_hash_comments(raw_text))

    try:
        data = json.loads(cleaned_text)
    except json.JSONDecodeError as error:
        raise ValueError(
            f"Invalid surah names file format in {path}: {error}. "
            "Expected an object like {\"الفاتحة\": 1, ...}."
        ) from error

    if not isinstance(data, dict):
        raise ValueError(f"Invalid surah names format in {path}. Expected a JSON object.")

    surah_names: dict[int, str] = {}
    for name, number in data.items():
        if not isinstance(name, str) or not name.strip():
            raise ValueError(f"Invalid surah name key in {path}: {name!r}")
        parsed_number = parse_number(number)
        if parsed_number is None:
            raise ValueError(f"Invalid surah number for {name!r} in {path}: {number!r}")
        if parsed_number < 1 or parsed_number > 114:
            raise ValueError(
                f"Surah number out of range for {name!r} in {path}: {parsed_number}. Expected 1..114"
            )
        if parsed_number in surah_names:
            raise ValueError(
                f"Duplicate surah number {parsed_number} in {path} "
                f"for names {surah_names[parsed_number]!r} and {name!r}."
            )
        surah_names[parsed_number] = name.strip()

    missing = [number for number in range(1, 115) if number not in surah_names]
    if missing:
        raise ValueError(
            f"Surah names file {path} is missing mappings for surah numbers: {missing}"
        )

    return surah_names


def parse_number(value: Any) -> int | None:
    if isinstance(value, int):
        return value
    if isinstance(value, float) and value.is_integer():
        return int(value)
    if isinstance(value, str):
        text = value.strip()
        if not text:
            return None
        try:
            number = int(text)
            return number
        except ValueError:
            return None
    return None


def is_standalone_marker_token(value: str) -> bool:
    token = value.strip()
    if not token:
        return False
    return not any(character.isalpha() or character.isdigit() for character in token)


def prepare_words_by_id(word_data: dict) -> dict[int, dict]:
    words_by_id: dict[int, dict] = {}
    for item in word_data.values():
        word_id = parse_number(item.get("id"))
        if word_id is None:
            continue
        words_by_id[word_id] = item
    return words_by_id


def get_ayah_line_words(
    first_word_id: int,
    last_word_id: int,
    active_surah_number: int,
    words_by_id: dict[int, dict],
) -> list[PreparedWord]:
    words: list[PreparedWord] = []
    for word_id in range(first_word_id, last_word_id + 1):
        word = words_by_id.get(word_id)
        if not word:
            continue

        text = str(word.get("text", "")).strip()
        if not text:
            continue

        word_surah = parse_number(word.get("surah"))
        if word_surah != active_surah_number:
            continue

        ayah_number = parse_number(word.get("ayah"))
        if ayah_number is None:
            continue

        if words and is_standalone_marker_token(text):
            last = words[-1]
            words[-1] = PreparedWord(text=f"{last.text} {text}".strip(), ayah_number=last.ayah_number)
            continue

        words.append(PreparedWord(text=text, ayah_number=ayah_number))

    return words


def build_prepared_layout(
    layout_data: list[dict],
    words_by_id: dict[int, dict],
    surah_names: dict[int, str],
) -> dict[int, list[PreparedLine | None]]:
    by_page: dict[int, list[PreparedLine | None]] = defaultdict(lambda: [None] * 16)
    active_surah_number: int | None = None

    for row in layout_data:
        page_number = parse_number(row.get("page_number"))
        line_number = parse_number(row.get("line_number"))
        if page_number is None or line_number is None or line_number < 1 or line_number > 16:
            continue

        line_type = str(row.get("line_type", "")).strip()

        if line_type == "surah_name":
            surah_number = parse_number(row.get("surah_number"))
            active_surah_number = surah_number
            if surah_number is None:
                continue
            text = surah_names.get(surah_number)
            if text is None:
                raise ValueError(
                    f"Missing surah name for surah_number={surah_number} while preparing page {page_number}, line {line_number}."
                )
            prepared = PreparedLine(
                page_number=page_number,
                line_number=line_number,
                line_type="surah_name",
                is_centered=True,
                surah_number=surah_number,
                text=text,
                words=[],
            )
            by_page[page_number][line_number - 1] = prepared
            continue

        if active_surah_number is None:
            continue

        if line_type == "basmallah":
            prepared = PreparedLine(
                page_number=page_number,
                line_number=line_number,
                line_type="basmallah",
                is_centered=True,
                surah_number=active_surah_number,
                text=BASMALLAH_TEXT,
                words=[],
            )
            by_page[page_number][line_number - 1] = prepared
            continue

        first_word_id = parse_number(row.get("first_word_id"))
        last_word_id = parse_number(row.get("last_word_id"))
        if first_word_id is None or last_word_id is None or first_word_id > last_word_id:
            continue

        words = get_ayah_line_words(first_word_id, last_word_id, active_surah_number, words_by_id)
        if not words:
            continue

        text = " ".join(word.text for word in words).strip()
        if not text:
            continue

        is_centered = parse_number(row.get("is_centered")) == 1
        prepared = PreparedLine(
            page_number=page_number,
            line_number=line_number,
            line_type="ayah",
            is_centered=is_centered,
            surah_number=active_surah_number,
            text=text,
            words=words,
        )
        by_page[page_number][line_number - 1] = prepared

    return by_page


def load_font(font_path: Path, size: int):
    try:
        return ImageFont.truetype(str(font_path), size=size, layout_engine=ImageFont.Layout.RAQM)
    except Exception as error:
        raise RuntimeError(
            "Failed to load Arabic font with RAQM layout engine. "
            f"font={font_path}, size={size}. "
            "Make sure Pillow is installed with RAQM/libraqm support and the font file is valid."
        ) from error


def fit_font_to_line(
    draw: ImageDraw.ImageDraw,
    text: str,
    font_path: Path,
    max_width: int,
    max_height: int,
    start_size: int,
    stroke_width: int,
    min_size: int = 18,
):
    size = max(start_size, min_size)
    while size >= min_size:
        font = load_font(font_path, size)
        left, top, right, bottom = draw.textbbox(
            (0, 0),
            text,
            font=font,
            direction="rtl",
            language="ar",
            stroke_width=stroke_width,
        )
        width = right - left
        height = bottom - top
        if width <= max_width and height <= max_height:
            return font
        size -= 1

    return load_font(font_path, min_size)


def text_bbox(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, stroke_width: int):
    return draw.textbbox(
        (0, 0),
        text,
        font=font,
        direction="rtl",
        language="ar",
        stroke_width=stroke_width,
    )


def text_width(draw: ImageDraw.ImageDraw, text: str, font: ImageFont.FreeTypeFont, stroke_width: int) -> int:
    left, _, right, _ = text_bbox(draw, text, font, stroke_width)
    return right - left


def anchored_text_bbox(
    draw: ImageDraw.ImageDraw,
    text: str,
    x: float,
    y: float,
    font: ImageFont.FreeTypeFont,
    stroke_width: int,
    anchor: str,
) -> tuple[float, float, float, float]:
    left, top, right, bottom = draw.textbbox(
        (x, y),
        text,
        font=font,
        anchor=anchor,
        direction="rtl",
        language="ar",
        stroke_width=stroke_width,
    )
    return float(left), float(top), float(right), float(bottom)


def compute_word_layout(
    draw: ImageDraw.ImageDraw,
    words: list[PreparedWord],
    font: ImageFont.FreeTypeFont,
    stroke_width: int,
    y_center: float,
    right_anchor: float,
    justify_target_width: int | None,
) -> list[dict[str, float | str | int]]:
    if not words:
        return []

    word_widths = [text_width(draw, word.text, font, stroke_width) for word in words]
    words_total_width = float(sum(word_widths))
    gap_count = len(words) - 1
    space_width = float(text_width(draw, " ", font, stroke_width))

    gap_width = space_width
    if justify_target_width is not None and gap_count > 0 and words_total_width < justify_target_width:
        gap_width = float(justify_target_width - words_total_width) / gap_count

    positioned: list[dict[str, float | str | int]] = []
    x_cursor = float(right_anchor)

    for index, word in enumerate(words):
        left, top, right, bottom = anchored_text_bbox(
            draw=draw,
            text=word.text,
            x=x_cursor,
            y=y_center,
            font=font,
            stroke_width=stroke_width,
            anchor="rm",
        )
        positioned.append(
            {
                "text": word.text,
                "ayah_number": float(word.ayah_number),
                "x_left": left,
                "x_right": right,
                "y_top": top,
                "y_bottom": bottom,
                "draw_x": x_cursor,
            }
        )
        x_cursor -= word_widths[index]
        if index < len(words) - 1:
            x_cursor -= gap_width

    return positioned


def draw_ayah_words_justified(
    draw: ImageDraw.ImageDraw,
    words: list[PreparedWord],
    font: ImageFont.FreeTypeFont,
    stroke_width: int,
    right_margin: int,
    y_center: float,
    target_width: int | None,
    text_color: tuple[int, int, int],
) -> list[dict[str, float | str | int]]:
    positioned_words = compute_word_layout(
        draw=draw,
        words=words,
        font=font,
        stroke_width=stroke_width,
        y_center=y_center,
        right_anchor=right_margin,
        justify_target_width=target_width,
    )
    for item in positioned_words:
        draw.text(
            (float(item["draw_x"]), y_center),
            str(item["text"]),
            fill=text_color,
            font=font,
            anchor="rm",
            direction="rtl",
            language="ar",
            stroke_width=stroke_width,
            stroke_fill=text_color,
        )
    return positioned_words


def collect_line_ayah_boxes(
    positioned_words: list[dict[str, float | str | int]],
    surah_number: int,
    line_number: int,
    width: int,
    height: int,
) -> list[AyahBox]:
    ayah_bounds: dict[int, dict[str, float]] = {}

    for item in positioned_words:
        ayah_number = int(float(item["ayah_number"]))
        left = float(item["x_left"])
        right = float(item["x_right"])
        top = float(item["y_top"])
        bottom = float(item["y_bottom"])
        bounds = ayah_bounds.get(ayah_number)
        if bounds is None:
            ayah_bounds[ayah_number] = {
                "left": left,
                "right": right,
                "top": top,
                "bottom": bottom,
            }
        else:
            bounds["left"] = min(bounds["left"], left)
            bounds["right"] = max(bounds["right"], right)
            bounds["top"] = min(bounds["top"], top)
            bounds["bottom"] = max(bounds["bottom"], bottom)

    result: list[AyahBox] = []
    for ayah_number, bounds in sorted(ayah_bounds.items()):
        box_width = max(0.0, bounds["right"] - bounds["left"])
        box_height = max(0.0, bounds["bottom"] - bounds["top"])
        if box_width <= 0 or box_height <= 0:
            continue
        result.append(
            AyahBox(
                ref_key=f"{surah_number}:{ayah_number}",
                surah_number=surah_number,
                ayah_number=ayah_number,
                line_number=line_number,
                x=max(0.0, min(1.0, bounds["left"] / width)),
                y=max(0.0, min(1.0, bounds["top"] / height)),
                width=max(0.0, min(1.0, box_width / width)),
                height=max(0.0, min(1.0, box_height / height)),
            )
        )
    return result


def render_page(
    page_number: int,
    page_slots: list[PreparedLine | None],
    font_path: Path,
    width: int,
    height: int,
    panel_vertical_padding_ratio: float,
    text_vertical_padding_ratio: float,
    max_ayah_font_size_ratio: float,
    max_centered_ayah_font_size_ratio: float,
    max_basmallah_font_size_ratio: float,
    stroke_width_ratio: float,
    header_stroke_width_ratio: float,
) -> tuple[Image.Image, list[AyahBox]]:
    # Cream background and single border for premium look
    page_bg = (252, 250, 245)  # Cream
    border_dark = (60, 50, 40)  # Deep, warm brownish-gray
    rule_color = (150, 150, 150)
    text_color = (16, 16, 16)

    image = Image.new("RGB", (width, height), page_bg)
    draw = ImageDraw.Draw(image)

    panel_left = int(width * 0.024)
    panel_right = int(width * 0.976)
    panel_vertical_padding_ratio = max(0.0, min(0.2, panel_vertical_padding_ratio))
    panel_top = int(height * panel_vertical_padding_ratio)
    panel_bottom = int(height * (1.0 - panel_vertical_padding_ratio))

    outer_border_width = max(5, int(width * 0.0018))
    inner_border_inset = max(14, int(width * 0.0075))

    # Draw only a single main border
    draw.rectangle(
        [(panel_left, panel_top), (panel_right, panel_bottom)],
        outline=border_dark,
        width=outer_border_width,
    )

    # Use the area inside the main border for text
    text_area_padding_x = int((panel_right - panel_left) * 0.02)
    text_vertical_padding_ratio = max(0.0, min(0.2, text_vertical_padding_ratio))
    text_area_padding_y = int((panel_bottom - panel_top) * text_vertical_padding_ratio)

    left_margin = panel_left + text_area_padding_x
    right_margin = panel_right - text_area_padding_x
    top_margin = panel_top + text_area_padding_y
    bottom_margin = panel_bottom - text_area_padding_y

    line_area_height = bottom_margin - top_margin
    line_slot_height = line_area_height / 16.0
    max_line_width = right_margin - left_margin
    page_ayah_boxes: list[AyahBox] = []
    max_ayah_font_size_ratio = max(0.25, min(1.0, max_ayah_font_size_ratio))
    max_centered_ayah_font_size_ratio = max(0.25, min(1.0, max_centered_ayah_font_size_ratio))

    for boundary in range(17):
        y = int(top_margin + boundary * line_slot_height)
        draw.line(
            [(left_margin, y), (right_margin, y)],
            fill=rule_color,
            width=max(1, int(width * 0.0009)),
        )

    for line_number in range(1, 17):
        row = page_slots[line_number - 1]
        if not row:
            continue

        text = row.text
        if not text:
            continue

        line_type = row.line_type
        stroke_width_ratio = max(0.0, min(0.01, stroke_width_ratio))
        header_stroke_width_ratio = max(0.0, min(0.01, header_stroke_width_ratio))
        if line_type in {"surah_name", "basmallah"}:
            stroke_width = max(0, int(width * header_stroke_width_ratio))
        else:
            stroke_width = max(0, int(width * stroke_width_ratio))
        if line_type in {"surah_name", "basmallah"}:
            start_font_size = int(line_slot_height * 0.72)
        else:
            start_font_size = int(line_slot_height * 0.92)

        font = fit_font_to_line(
            draw=draw,
            text=text,
            font_path=font_path,
            max_width=max_line_width,
            max_height=int(line_slot_height * 0.98),
            start_size=max(20, start_font_size),
            stroke_width=stroke_width,
            min_size=16,
        )

        if line_type == "ayah":
            max_ayah_size = max(16, int(line_slot_height * max_ayah_font_size_ratio))
            current_size = getattr(font, "size", max_ayah_size)
            if current_size > max_ayah_size:
                font = load_font(font_path, max_ayah_size)
        elif line_type == "basmallah":
            max_basmallah_size = max(16, int(line_slot_height * max_basmallah_font_size_ratio))
            current_size = getattr(font, "size", max_basmallah_size)
            if current_size > max_basmallah_size:
                font = load_font(font_path, max_basmallah_size)

        is_centered = row.is_centered or line_type in {"surah_name", "basmallah"} or len(row.words) <= 1

        if line_type == "ayah" and is_centered:
            max_centered_ayah_size = max(16, int(line_slot_height * max_centered_ayah_font_size_ratio))
            current_size = getattr(font, "size", max_centered_ayah_size)
            if current_size > max_centered_ayah_size:
                font = load_font(font_path, max_centered_ayah_size)

        y_center = top_margin + (line_number - 0.5) * line_slot_height
        if line_type == "ayah" and row.words:
            justify_target_width = int(max_line_width * 0.99) if (not is_centered and len(row.words) > 1) else None
            right_anchor = float(right_margin)
            if is_centered:
                total_words_width = sum(text_width(draw, word.text, font, stroke_width) for word in row.words)
                default_gap = text_width(draw, " ", font, stroke_width)
                total_width = total_words_width + (max(0, len(row.words) - 1) * default_gap)
                right_anchor = (width / 2.0) + (total_width / 2.0)

            positioned = draw_ayah_words_justified(
                draw=draw,
                words=row.words,
                font=font,
                stroke_width=stroke_width,
                right_margin=int(round(right_anchor)),
                y_center=y_center,
                target_width=justify_target_width,
                text_color=text_color,
            )
            line_boxes = collect_line_ayah_boxes(
                positioned_words=positioned,
                surah_number=row.surah_number,
                line_number=line_number,
                width=width,
                height=height,
            )
            page_ayah_boxes.extend(line_boxes)
        else:
            if is_centered:
                x = width // 2
                anchor = "mm"
            else:
                x = right_margin
                anchor = "rm"

            draw.text(
                (x, y_center),
                text,
                fill=text_color,
                font=font,
                anchor=anchor,
                direction="rtl",
                language="ar",
                stroke_width=stroke_width,
                stroke_fill=text_color,
            )

    return image, page_ayah_boxes


def render_cover_page(width: int, height: int, font_path: Path, cover_text: str) -> Image.Image:
    image = Image.new("RGB", (width, height), (252, 250, 245))
    draw = ImageDraw.Draw(image)

    text = cover_text.strip() or COVER_TEXT_DEFAULT
    stroke_width = max(0, int(width * 0.00085))
    max_width = int(width * 0.72)
    max_height = int(height * 0.28)
    start_size = int(min(width, height) * 0.16)

    font = fit_font_to_line(
        draw=draw,
        text=text,
        font_path=font_path,
        max_width=max_width,
        max_height=max_height,
        start_size=max(28, start_size),
        stroke_width=stroke_width,
        min_size=20,
    )

    draw.text(
        (width / 2.0, height / 2.0),
        text,
        fill=(10, 10, 10),
        font=font,
        anchor="mm",
        direction="rtl",
        language="ar",
        stroke_width=stroke_width,
        stroke_fill=(10, 10, 10),
    )
    return image

def save_webp(image: Image.Image, out_path: Path, quality: int, lossless: bool):
    out_path.parent.mkdir(parents=True, exist_ok=True)
    quality = max(0, min(100, int(quality)))
    image.save(out_path, format="WEBP", quality=quality, method=6, lossless=lossless)
    return out_path

def write_page_coords_json(out_dir: Path, page_number: int, image_filename: str, ayah_boxes: list[AyahBox]):
    out_dir.mkdir(parents=True, exist_ok=True)
    payload = {
        "pageNumber": page_number,
        "image": image_filename,
        "ayahBoxes": [
            {
                "refKey": box.ref_key,
                "surahNumber": box.surah_number,
                "ayahNumber": box.ayah_number,
                "lineNumber": box.line_number,
                "bbox": {
                    "x": round(box.x, 6),
                    "y": round(box.y, 6),
                    "width": round(box.width, 6),
                    "height": round(box.height, 6),
                },
            }
            for box in ayah_boxes
        ],
    }
    out_path = out_dir / f"page_{page_number:03d}.json"
    with out_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    return out_path


def write_ayah_ref_index_json(out_dir: Path, page_to_boxes: dict[int, list[AyahBox]]):
    out_dir.mkdir(parents=True, exist_ok=True)

    refs: dict[str, dict[str, Any]] = {}
    for page_number, boxes in page_to_boxes.items():
        segments_per_ref: dict[str, int] = defaultdict(int)
        sample_box_by_ref: dict[str, AyahBox] = {}

        for box in boxes:
            segments_per_ref[box.ref_key] += 1
            if box.ref_key not in sample_box_by_ref:
                sample_box_by_ref[box.ref_key] = box

        for ref_key, segment_count in segments_per_ref.items():
            sample_box = sample_box_by_ref[ref_key]
            existing = refs.get(ref_key)
            page_segment = {"pageNumber": page_number, "segmentCount": segment_count}
            if existing is None:
                refs[ref_key] = {
                    "refKey": ref_key,
                    "surahNumber": sample_box.surah_number,
                    "ayahNumber": sample_box.ayah_number,
                    "pages": [page_segment],
                }
            else:
                existing["pages"].append(page_segment)

    payload = {
        "ayahRefs": [
            {
                "refKey": item["refKey"],
                "surahNumber": item["surahNumber"],
                "ayahNumber": item["ayahNumber"],
                "pages": sorted(item["pages"], key=lambda p: p["pageNumber"]),
            }
            for _, item in sorted(refs.items(), key=lambda kv: (kv[1]["surahNumber"], kv[1]["ayahNumber"]))
        ]
    }
    out_path = out_dir / "ayah_ref_index.json"
    with out_path.open("w", encoding="utf-8") as handle:
        json.dump(payload, handle, ensure_ascii=False, indent=2)
    return out_path


def render_bundle_for_resolution(
    args,
    layout_index: dict[int, list[PreparedLine | None]],
    para_page_starts: list[int],
    bundle_out_dir: Path,
    pages_to_render: list[int],
    width: int,
    height: int,
) -> Path:
    resolution_out_dir = bundle_out_dir / f"{width}x{height}"
    resolution_out_dir.mkdir(parents=True, exist_ok=True)
    page_to_boxes: dict[int, list[AyahBox]] = {}

    if args.add_cover_page:
        cover_image = render_cover_page(
            width=width,
            height=height,
            font_path=args.font_path,
            cover_text=args.cover_text,
        )
        cover_para_dir = resolution_out_dir / get_para_folder_name(1, para_page_starts)
        cover_para_dir.mkdir(parents=True, exist_ok=True)
        cover_out_path = cover_para_dir / "page_001.webp"
        save_webp(cover_image, cover_out_path, args.webp_quality, args.webp_lossless)
        cover_coords_path = write_page_coords_json(
            out_dir=cover_para_dir,
            page_number=1,
            image_filename=cover_out_path.name,
            ayah_boxes=[],
        )
        page_to_boxes[1] = []
        print(f"Saved: {cover_out_path}")
        print(f"Saved: {cover_coords_path}")

    shift = 1 if args.add_cover_page else 0
    out_start_page = pages_to_render[0] + shift
    out_end_page = pages_to_render[-1] + shift
    print(
        f"Rendering source pages {pages_to_render[0]}-{pages_to_render[-1]} "
        f"as output pages {out_start_page}-{out_end_page} to {resolution_out_dir}..."
    )

    for page_number in pages_to_render:
        page_slots = layout_index.get(page_number, [None] * 16)
        image, ayah_boxes = render_page(
            page_number=page_number,
            page_slots=page_slots,
            font_path=args.font_path,
            width=width,
            height=height,
            panel_vertical_padding_ratio=args.panel_vertical_padding_ratio,
            text_vertical_padding_ratio=args.text_vertical_padding_ratio,
            max_ayah_font_size_ratio=args.max_ayah_font_size_ratio,
            max_centered_ayah_font_size_ratio=args.max_centered_ayah_font_size_ratio,
            max_basmallah_font_size_ratio=args.max_basmallah_font_size_ratio,
            stroke_width_ratio=args.stroke_width_ratio,
            header_stroke_width_ratio=args.header_stroke_width_ratio,
        )

        output_page_number = page_number + shift
        para_dir = resolution_out_dir / get_para_folder_name(output_page_number, para_page_starts)
        para_dir.mkdir(parents=True, exist_ok=True)
        out_path = para_dir / f"page_{output_page_number:03d}.webp"
        save_webp(image, out_path, args.webp_quality, args.webp_lossless)
        coords_path = write_page_coords_json(
            out_dir=para_dir,
            page_number=output_page_number,
            image_filename=out_path.name,
            ayah_boxes=ayah_boxes,
        )
        page_to_boxes[output_page_number] = ayah_boxes
        print(f"Saved: {out_path}")
        print(f"Saved: {coords_path}")

    ref_index_path = write_ayah_ref_index_json(resolution_out_dir, page_to_boxes)
    print(f"Saved: {ref_index_path}")

    return resolution_out_dir


def main():
    args = parse_args()

    if not args.font_path.exists():
        raise FileNotFoundError(f"Font not found: {args.font_path}")
    if not args.layout_path.exists():
        raise FileNotFoundError(f"Layout file not found: {args.layout_path}")
    if not args.word_by_word_path.exists():
        raise FileNotFoundError(f"Word-by-word file not found: {args.word_by_word_path}")
    if not args.surah_names_path.exists():
        raise FileNotFoundError(f"Surah names file not found: {args.surah_names_path}")

    layout_data = load_json(args.layout_path)
    word_data = load_json(args.word_by_word_path)
    surah_names = load_surah_names(args.surah_names_path)

    words_by_id = prepare_words_by_id(word_data)
    layout_index = build_prepared_layout(layout_data, words_by_id, surah_names)
    para_page_starts = load_para_page_starts()

    max_page = max(layout_index)
    end_page = min(args.start_page + args.page_count - 1, max_page)
    pages_to_render = list(range(args.start_page, end_page + 1))

    if not pages_to_render:
        raise ValueError("No pages selected for rendering.")

    bundle_out_dir = resolve_bundle_output_root(args.out_dir, args.coords_out_dir)
    bundle_out_dir.mkdir(parents=True, exist_ok=True)
    if args.interactive:
        selected_resolutions = prompt_resolution_presets()
        archive_format = prompt_archive_format()
    else:
        selected_resolutions = [(args.width, args.height)]
        archive_format = args.archive_format

    for width, height in selected_resolutions:
        print(f"\n=== Exporting resolution {width}x{height} ===")
        resolution_out_dir = render_bundle_for_resolution(
            args=args,
            layout_index=layout_index,
            para_page_starts=para_page_starts,
            bundle_out_dir=bundle_out_dir,
            pages_to_render=pages_to_render,
            width=width,
            height=height,
        )

        archive_path = create_archive(resolution_out_dir, archive_format)
        if archive_path:
            print(f"Saved archive: {archive_path}")

    print("Done.")


if __name__ == "__main__":
    main()
