#!/usr/bin/env python3
"""Import deliberative/withheld CORA documents into the static archive."""

from __future__ import annotations

import json
import os
import re
import shutil
import subprocess
import sys
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path
from typing import Any
from xml.sax.saxutils import escape
from zoneinfo import ZoneInfo

DEFAULT_DEPS = Path("/private/tmp/alameda-msg-deps")
if DEFAULT_DEPS.exists():
    sys.path.insert(0, str(DEFAULT_DEPS))

try:
    import extract_msg
except ImportError as exc:
    raise SystemExit(
        "Missing extract_msg. Install it with:\n"
        "  python3 -m pip install --target /private/tmp/alameda-msg-deps extract-msg"
    ) from exc

try:
    from reportlab.lib.colors import HexColor
    from reportlab.lib.enums import TA_LEFT
    from reportlab.lib.pagesizes import letter
    from reportlab.lib.styles import ParagraphStyle, getSampleStyleSheet
    from reportlab.lib.units import inch
    from reportlab.platypus import HRFlowable, Paragraph, SimpleDocTemplate, Spacer
except ImportError as exc:
    raise SystemExit("Missing reportlab. Run with the bundled Codex Python runtime.") from exc


REPO_ROOT = Path(__file__).resolve().parents[1]
SOURCE_ROOT = Path(
    os.environ.get(
        "WITHHELD_SOURCE_ROOT",
        "/Users/davidmintzer/Documents/GitHub/Withheld documents",
    )
)
OUTPUT_ROOT = REPO_ROOT / "Alameda CORA pdf files" / "withheld_documents"
JSON_PATH = REPO_ROOT / "alameda_emails.json"
MANIFEST_PATH = REPO_ROOT / "withheld_documents.json"
BRIEFING_GROUP = "four emails preppng for briefing"

CONTROL_CHARS = re.compile(r"[\x00-\x08\x0b\x0c\x0e-\x1f\x7f-\x9f]")


def clean_text(value: Any) -> str:
    if value is None:
        return ""
    text = str(value)
    if text == "None":
        return ""
    return CONTROL_CHARS.sub("", text).strip()


def sanitize_filename(name: str, max_length: int = 120) -> str:
    name = re.sub(r'[<>:"/\\|?*]', "_", name)
    name = re.sub(r"\s+", " ", name).strip()
    name = name.rstrip(". ")
    if not name:
        name = "untitled"
    return name[:max_length].rstrip()


def clean_email_body(text: str) -> str:
    if not text:
        return ""
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(
        r"https?://us-phishalarm[^\s>]*[^\s]*>?\s*Report\s+Suspicious\s*",
        "",
        text,
        flags=re.IGNORECASE,
    )
    text = re.sub(
        r"ZjQcmQRYFpfptBannerStart.*?ZjQcmQRYFpfptBannerEnd",
        "",
        text,
        flags=re.DOTALL,
    )
    text = re.sub(r"ZjQcmQRYFpfptBannerEnd", "", text)

    def unwrap_urldefense(match: re.Match[str]) -> str:
        full_url = match.group(0)
        inner = re.search(r"/v3/__(.+?)__;", full_url)
        return inner.group(1) if inner else full_url

    text = re.sub(r"https?://urldefense\.com/v3/__[^\s<>]*", unwrap_urldefense, text)
    text = re.sub(r"\n{4,}", "\n\n\n", text)
    return CONTROL_CHARS.sub("", text).strip()


def remove_all_snippets(body: str) -> str:
    if not body or len(body) < 100:
        return body

    boundary_zones = [0]
    for match in re.finditer(r"Subject:", body):
        pos = match.end()
        for _ in range(5):
            newline = body.find("\n", pos)
            if newline == -1:
                break
            pos = newline + 1
            boundary_zones.append(pos)

    result = body
    offset = 0
    snippets_found: list[tuple[int, int]] = []

    for boundary in boundary_zones:
        adj_boundary = boundary - offset
        if adj_boundary < 0 or adj_boundary >= len(result):
            continue
        remaining = result[adj_boundary:]
        if len(remaining) < 100:
            continue

        normalized = re.sub(r"\s+", " ", remaining).strip()
        if len(normalized) < 60:
            continue

        fingerprint = normalized[:30]
        idx = normalized[20:800].find(fingerprint)
        if idx == -1:
            continue

        snippet_end_norm = idx + 20
        repeat_start = snippet_end_norm
        match_len = 0
        for j in range(min(200, len(normalized) - repeat_start)):
            if normalized[j] == normalized[repeat_start + j]:
                match_len += 1
            else:
                break
        if match_len < 25:
            continue

        norm_pos = 0
        orig_pos = 0
        while orig_pos < len(remaining) and norm_pos < snippet_end_norm:
            if remaining[orig_pos : orig_pos + 1].isspace():
                while orig_pos < len(remaining) and remaining[orig_pos : orig_pos + 1].isspace():
                    orig_pos += 1
                norm_pos += 1
            else:
                orig_pos += 1
                norm_pos += 1

        while orig_pos < len(remaining) and remaining[orig_pos : orig_pos + 1].isspace():
            orig_pos += 1

        abs_start = adj_boundary
        abs_end = adj_boundary + orig_pos
        if any(abs_start < end and abs_end > start for start, end in snippets_found):
            continue

        snippets_found.append((abs_start, abs_end))
        result = result[:adj_boundary] + remaining[orig_pos:]
        offset += orig_pos

    return result


def parse_date(value: Any) -> datetime | None:
    text = clean_text(value)
    if not text:
        return None

    try:
        return parsedate_to_datetime(text)
    except (TypeError, ValueError, IndexError):
        pass

    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00"))
    except ValueError:
        pass

    text_without_fraction = re.sub(r"\.\d+", "", text)
    try:
        return datetime.fromisoformat(text_without_fraction.replace("Z", "+00:00"))
    except ValueError:
        return None


def format_json_date(value: Any) -> str:
    parsed = parse_date(value)
    if parsed is None:
        return clean_text(value)
    return parsed.isoformat(sep=" ", timespec="seconds")


def parse_briefing_date(value: str) -> str:
    value = clean_text(value)
    if not value:
        return ""
    try:
        parsed = datetime.strptime(value, "%A, %B %d, %Y %I:%M %p")
    except ValueError:
        return value
    return parsed.replace(tzinfo=ZoneInfo("America/Denver")).isoformat(
        sep=" ",
        timespec="seconds",
    )


def filename_timestamp(value: Any) -> str:
    parsed = parse_date(value)
    if parsed is None:
        return "no-date"
    return parsed.strftime("%Y-%m-%d_%H-%M-%S")


def unique_path(path: Path) -> Path:
    if not path.exists():
        return path
    stem = path.stem
    suffix = path.suffix
    parent = path.parent
    counter = 1
    while True:
        candidate = parent / f"{stem} ({counter}){suffix}"
        if not candidate.exists():
            return candidate
        counter += 1


def repo_relative(path: Path) -> str:
    return path.relative_to(REPO_ROOT).as_posix()


def group_name(path: Path) -> str:
    try:
        rel_parent = path.parent.relative_to(SOURCE_ROOT)
    except ValueError:
        return "Withheld documents"
    parts = [part for part in rel_parent.parts if not part.startswith("For D. Mintzer")]
    if not parts:
        return "Withheld documents"
    return parts[-1]


def split_sender(sender: str) -> tuple[str, str]:
    sender = clean_text(sender)
    if not sender:
        return "Unknown sender", ""

    angle_match = re.search(r"<([^<>@\s]+@[^<>@\s]+)>", sender)
    if angle_match:
        email = angle_match.group(1).strip()
        name = sender[: angle_match.start()].strip().strip('"')
        return name or email, email

    email_match = re.search(r"([^<>\s]+@[^<>\s]+)", sender)
    if email_match:
        email = email_match.group(1).strip()
        name = sender.replace(email, "").strip().strip("<>").strip().strip('"')
        return name or email, email

    return sender, ""


def build_pdf(
    pdf_path: Path,
    *,
    subject: str,
    sender: str,
    to: str,
    cc: str,
    date: str,
    body: str,
    attachments: list[dict[str, str]],
) -> None:
    doc = SimpleDocTemplate(
        str(pdf_path),
        pagesize=letter,
        leftMargin=0.72 * inch,
        rightMargin=0.72 * inch,
        topMargin=0.72 * inch,
        bottomMargin=0.72 * inch,
    )
    styles = getSampleStyleSheet()
    subject_style = ParagraphStyle(
        "EmailSubject",
        parent=styles["Heading1"],
        fontSize=14,
        leading=18,
        textColor=HexColor("#1a1a1a"),
        spaceAfter=6,
    )
    header_style = ParagraphStyle(
        "Header",
        parent=styles["Normal"],
        fontSize=9.5,
        leading=13,
        textColor=HexColor("#1a1a1a"),
    )
    body_style = ParagraphStyle(
        "EmailBody",
        parent=styles["Normal"],
        fontSize=9.5,
        leading=13,
        textColor=HexColor("#222222"),
        alignment=TA_LEFT,
        spaceAfter=7,
    )
    small_style = ParagraphStyle(
        "Small",
        parent=styles["Normal"],
        fontSize=8.5,
        leading=11,
        textColor=HexColor("#555555"),
    )

    story = [Paragraph(escape(subject or "(No Subject)"), subject_style), Spacer(1, 4)]

    def add_header(label: str, value: str) -> None:
        if value:
            story.append(Paragraph(f"<b>{escape(label)}:</b> {escape(value)}", header_style))

    add_header("From", sender)
    add_header("To", to)
    add_header("CC", cc)
    add_header("Date", date)
    if attachments:
        add_header("Attachments", ", ".join(att["n"] for att in attachments))

    story.append(
        HRFlowable(
            width="100%",
            thickness=1,
            color=HexColor("#cccccc"),
            spaceAfter=10,
            spaceBefore=8,
        )
    )

    blocks = re.split(r"\n\s*\n", body or "(No body text)")
    for block in blocks:
        block = re.sub(r"\s*\n\s*", "<br/>", escape(block.strip()))
        if block:
            story.append(Paragraph(block, body_style))

    if attachments:
        story.append(Spacer(1, 8))
        story.append(Paragraph("<b>Extracted attachments</b>", header_style))
        for att in attachments:
            story.append(Paragraph(escape(att["n"]), small_style))

    doc.build(story)


def extract_attachments(msg: Any, email_dir: Path, pdf_stem: str) -> list[dict[str, str]]:
    attachments: list[dict[str, str]] = []
    msg_attachments = msg.attachments or []
    if not msg_attachments:
        return attachments

    attach_dir = email_dir / f"{sanitize_filename(pdf_stem, 90)} Attachments"
    attach_dir.mkdir(parents=True, exist_ok=True)

    for attachment in msg_attachments:
        name = clean_text(
            getattr(attachment, "longFilename", "")
            or getattr(attachment, "shortFilename", "")
            or "unnamed_attachment"
        )
        safe_name = sanitize_filename(name, 110)
        data = getattr(attachment, "data", None)
        if data is None:
            continue
        if isinstance(data, str):
            data = data.encode("utf-8", errors="replace")

        dest = unique_path(attach_dir / safe_name)
        dest.write_bytes(data)
        attachments.append({"n": dest.name, "path": repo_relative(dest)})

    return attachments


def convert_msg(path: Path) -> tuple[dict[str, Any], list[dict[str, str]]]:
    msg = extract_msg.Message(str(path))
    try:
        subject = clean_text(msg.subject) or path.stem
        sender_full = clean_text(msg.sender)
        sender_name, sender_email = split_sender(sender_full)
        to = clean_text(msg.to)
        cc = clean_text(msg.cc)
        date = format_json_date(msg.date)
        body = clean_email_body(remove_all_snippets(clean_text(msg.body)))

        rel_parent = path.parent.relative_to(SOURCE_ROOT)
        output_dir = OUTPUT_ROOT / rel_parent
        output_dir.mkdir(parents=True, exist_ok=True)

        pdf_stem = f"{filename_timestamp(msg.date)} - {sanitize_filename(path.stem, 70)}"
        pdf_path = unique_path(output_dir / f"{pdf_stem}.pdf")
        attachments = extract_attachments(msg, output_dir, pdf_stem)

        build_pdf(
            pdf_path,
            subject=subject,
            sender=sender_full or sender_name,
            to=to,
            cc=cc,
            date=date,
            body=body,
            attachments=attachments,
        )

        search_text = " ".join(
            value
            for value in [subject, sender_name, sender_email, to, cc, date, body]
            if value
        )
        group = group_name(path)
        record = {
            "f": repo_relative(pdf_path),
            "s": subject,
            "n": sender_name,
            "e": sender_email,
            "to": to,
            "cc": cc,
            "d": date,
            "b": body,
            "pid": "",
            "att": attachments,
            "h": True,
            "c": "withheld",
            "g": group,
            "x": search_text,
        }
        manifest_items = [
            {
                "name": pdf_path.name,
                "path": repo_relative(pdf_path),
                "group": group,
                "type": "email",
                "date": date,
                "subject": subject,
            }
        ]
        for attachment in attachments:
            manifest_items.append(
                {
                    "name": attachment["n"],
                    "path": attachment["path"],
                    "group": group,
                    "type": "attachment",
                    "date": date,
                    "subject": f"Attachment: {attachment['n']}",
                    "source": subject,
                }
            )
        return record, manifest_items
    finally:
        msg.close()


def copy_support_documents() -> list[dict[str, str]]:
    manifest: list[dict[str, str]] = []
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    for path in sorted(SOURCE_ROOT.rglob("*")):
        if not path.is_file():
            continue
        if path.name == ".DS_Store" or path.suffix.lower() == ".msg":
            continue

        rel_path = path.relative_to(SOURCE_ROOT)
        dest = OUTPUT_ROOT / rel_path
        dest.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(path, dest)
        manifest.append(
            {
                "name": dest.name,
                "path": repo_relative(dest),
                "group": group_name(path),
                "type": "document",
            }
        )

    for cert_name in ["Certification Log.pdf", "Deliberative process certification.pdf"]:
        cert_path = REPO_ROOT / cert_name
        if cert_path.exists():
            manifest.append(
                {
                    "name": cert_name,
                    "path": cert_name,
                    "group": "Certification records",
                    "type": "certification",
                }
            )

    return manifest


def pdf_to_text(path: Path) -> str:
    try:
        result = subprocess.run(
            ["pdftotext", str(path), "-"],
            check=True,
            capture_output=True,
            text=True,
        )
    except (FileNotFoundError, subprocess.CalledProcessError):
        return ""
    return CONTROL_CHARS.sub("", result.stdout).strip()


def parse_briefing_pdf(path: Path) -> dict[str, str]:
    text = pdf_to_text(path)
    lines = [line.strip() for line in text.splitlines()]
    lines = [line for line in lines if line]
    stem = path.stem
    subject = stem
    sender = "Withheld briefing PDF"
    sent = ""
    to = ""
    cc = ""
    body = text

    label_names = {"From:", "Sent:", "To:", "Cc:", "Subject:", "Attachments:"}
    label_indices = [i for i, line in enumerate(lines) if line in label_names]
    if label_indices:
        start = max(label_indices) + 1
        header_labels = {line for line in lines[:start] if line in label_names}
        values = lines[start:]
        subject_idx = None
        for i, value in enumerate(values):
            if value.lower() == stem.lower():
                subject_idx = i
                break
        if subject_idx is None:
            normalized_stem = re.sub(r"[^a-z0-9]+", "", stem.lower())
            for i, value in enumerate(values):
                if re.sub(r"[^a-z0-9]+", "", value.lower()) == normalized_stem:
                    subject_idx = i
                    break
        if subject_idx is None and "Cc:" not in header_labels and len(values) >= 4:
            subject_idx = 3

        if len(values) >= 1:
            sender = values[0]
        if len(values) >= 2:
            sent = parse_briefing_date(values[1])
        if len(values) >= 3:
            to = values[2]
        if subject_idx is not None:
            subject = values[subject_idx]
            if subject_idx > 3:
                cc = "; ".join(values[3:subject_idx])
            body_start = subject_idx + 1
            if "Attachments:" in lines:
                while body_start < len(values) and not re.search(
                    r"^(hi|fyi|yes|one thing|from:|first )\b",
                    values[body_start],
                    flags=re.IGNORECASE,
                ):
                    body_start += 1
            body = "\n".join(values[body_start:]).strip() or text

    return {
        "subject": subject,
        "sender": sender,
        "date": sent,
        "to": to,
        "cc": cc,
        "body": clean_email_body(body),
    }


def build_briefing_pdf_records(manifest: list[dict[str, str]]) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for item in manifest:
        if item.get("group") != BRIEFING_GROUP:
            continue
        if not item.get("name", "").lower().endswith(".pdf"):
            continue
        pdf_path = REPO_ROOT / item["path"]
        parsed = parse_briefing_pdf(pdf_path)
        sender_name, sender_email = split_sender(parsed["sender"])
        search_text = " ".join(
            value
            for value in [
                parsed["subject"],
                sender_name,
                sender_email,
                parsed["to"],
                parsed["cc"],
                parsed["date"],
                parsed["body"],
            ]
            if value
        )
        records.append(
            {
                "f": item["path"],
                "s": parsed["subject"],
                "n": sender_name,
                "e": sender_email,
                "to": parsed["to"],
                "cc": parsed["cc"],
                "d": parsed["date"],
                "b": parsed["body"],
                "pid": "",
                "att": [],
                "h": True,
                "c": "withheld",
                "g": BRIEFING_GROUP,
                "x": search_text,
            }
        )
    return records


def sort_date(item: dict[str, Any]) -> datetime:
    parsed = parse_date(item.get("d"))
    if parsed is None:
        return datetime.min.replace(tzinfo=timezone.utc)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc)


def update_json(records: list[dict[str, Any]]) -> None:
    data = json.loads(JSON_PATH.read_text(encoding="utf-8"))
    data = [
        item
        for item in data
        if item.get("c") != "withheld"
        and not str(item.get("f", "")).startswith("Alameda CORA pdf files/withheld_documents/")
    ]
    data.extend(records)
    data.sort(key=sort_date)
    json_text = json.dumps(data, ensure_ascii=False, indent=3, separators=(",", ":"))
    json_text = json_text.replace('"att":[]', '"att":[\n         \n      ]')
    JSON_PATH.write_text(json_text + "\n", encoding="utf-8")


def write_manifest(items: list[dict[str, str]]) -> None:
    items.sort(key=lambda item: (item.get("group", ""), item.get("date", ""), item.get("name", "")))
    MANIFEST_PATH.write_text(
        json.dumps(items, ensure_ascii=False, indent=2, separators=(",", ":")) + "\n",
        encoding="utf-8",
    )


def main() -> None:
    if not SOURCE_ROOT.exists():
        raise SystemExit(f"Source folder not found: {SOURCE_ROOT}")
    if not JSON_PATH.exists():
        raise SystemExit(f"JSON file not found: {JSON_PATH}")

    msg_paths = sorted(SOURCE_ROOT.rglob("*.msg"))
    if not msg_paths:
        raise SystemExit(f"No .msg files found under: {SOURCE_ROOT}")

    if OUTPUT_ROOT.exists():
        shutil.rmtree(OUTPUT_ROOT)
    OUTPUT_ROOT.mkdir(parents=True, exist_ok=True)

    records: list[dict[str, Any]] = []
    manifest = copy_support_documents()
    records.extend(build_briefing_pdf_records(manifest))

    for path in msg_paths:
        record, manifest_items = convert_msg(path)
        records.append(record)
        manifest.extend(manifest_items)

    update_json(records)
    write_manifest(manifest)

    print(
        f"Imported {len(records)} withheld email/card records "
        f"from {len(msg_paths)} .msg files and briefing PDFs"
    )
    print(f"Wrote PDFs/docs under {OUTPUT_ROOT}")
    print(f"Updated {JSON_PATH.name} and {MANIFEST_PATH.name}")


if __name__ == "__main__":
    main()
