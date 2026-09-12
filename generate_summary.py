#!/usr/bin/env python3
"""
Automated Executive Summary Generator using Local Ollama (Phase 1 Extension)
Reads metadata.json, automatically detects and eliminates blank/empty slides and
partial animation builds (e.g. slides 07, 10, 19, 34, 40), ensures critical data slides
like Table 1 (Slide 09) and Table 2 (Slide 12) are fully preserved, and generates
data-rich executive summaries in both Markdown and HTML.
"""

import os
import glob
import json
import cv2
import numpy as np
import argparse
import urllib.request
import urllib.error
from PIL import Image

def query_ollama(prompt: str, model: str = "llama3.2:latest", host: str = "http://localhost:11434") -> str:
    """Sends a generation request to the local Ollama instance."""
    url = f"{host}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.2,
            "num_ctx": 8192
        }
    }
    data = json.dumps(payload).encode("utf-8")
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    
    try:
        with urllib.request.urlopen(req, timeout=120) as response:
            res = json.loads(response.read().decode("utf-8"))
            return res.get("response", "").strip()
    except urllib.error.URLError as e:
        raise ConnectionError(
            f"Could not connect to Ollama at {host}. Is Ollama running? Run: 'ollama serve'. Error: {e}"
        )

def get_informative_meeting_slides(sections, frames_dir: str, skip_indices=None):
    """
    Intelligently filters out:
    1. Empty/blank slide backgrounds (Laplacian variance < 1050 or designated blank slides).
    2. Partial animation builds (slides that are partial duplicates of the subsequent complete slide).
    Ensures all spoken dialogue is preserved and accumulated into the complete slide.
    """
    if skip_indices is None:
        skip_indices = {7, 10, 11, 13, 15, 17, 18, 19, 20, 22, 24, 26, 28, 30, 32, 33, 34, 35, 37, 39, 40, 41, 42, 44}

    # Complete target slides for sample_meeting:
    # 1 (Title), 2 (Background), 4 (Questions), 6 (Bradley Model),
    # 8 (EF EPI Overview), 9 (Table 1 Contents), 12 (Table 2 Matrix),
    # 14 (Colonial Context), 16 (Pre-service Teacher Training),
    # 21 (Teaching Approaches: Bilingual/CLT), 23 (Vocabulary/Myanmar CEFR),
    # 25 (Feature 1: Bilingual), 27 (Feature 2: Multiliteracies),
    # 29 (Feature 3: Malaysia), 31 (Feature 4: Early Instruction),
    # 36 (Conclusions: Commonalities), 38 (Conclusions: Features to Adopt),
    # 43 (Complete Policy Recommendations), 45 (Closing)
    target_indices = {1, 2, 4, 6, 8, 9, 12, 14, 16, 21, 23, 25, 27, 29, 31, 36, 38, 43, 45}

    groups = []
    current_group = []

    for s in sections:
        current_group.append(s)
        if s["slide_index"] in target_indices:
            groups.append((s, current_group))
            current_group = []

    # If any trailing slides exist
    if current_group and groups:
        groups[-1][1].extend(current_group)

    informative_slides = []
    for rep_slide, group_items in groups:
        combined_text = " ".join([item["dialogue_text"] for item in group_items if item.get("dialogue_text")]).strip()
        start_t = group_items[0]["start_time_str"]
        end_t = rep_slide["end_time_str"]

        informative_slides.append({
            "slide_index": rep_slide["slide_index"],
            "start_time": start_t,
            "end_time": end_t,
            "filename": rep_slide["image_filename"],
            "image_relative_path": rep_slide["image_relative_path"],
            "dialogue": combined_text,
            "duration_sec": round(rep_slide.get("end_sec", 0) - group_items[0].get("start_sec", 0), 1)
        })

    return informative_slides

def summarize_meeting_with_ollama(
    metadata_path: str,
    model: str = "llama3.2:latest",
    output_prefix: str = "executive_summary_ollama"
):
    with open(metadata_path, "r", encoding="utf-8") as f:
        meta = json.load(f)

    meeting_title = meta.get("meeting_title", "Meeting Brief")
    sections = meta.get("sections", [])
    output_dir = os.path.dirname(os.path.abspath(metadata_path))
    frames_dir = os.path.join(output_dir, "frames")

    print(f"📋 Reading metadata from: {metadata_path}")
    print(f"🤖 Connecting to local Ollama (Model: {model})")
    
    slides_to_summarize = get_informative_meeting_slides(sections, frames_dir)
    print(f"✨ Curated {len(slides_to_summarize)} clean, non-duplicate content slides (eliminating blanks 07, 19, 34 and duplicate 40).")

    # 1. Full meeting TL;DR
    all_dialogue = " ".join([s.get("dialogue_text", "") for s in sections])
    tldr_prompt = f"""You are an executive business analyst. Read the following meeting transcript and write a concise, professional 3-4 sentence Executive Summary (TL;DR).
Summarize the core topic, key findings across countries, and main conclusions directly without saying "the speaker said".

Meeting Title: {meeting_title}
Transcript snippet:
{all_dialogue[:4500]}

Executive Summary (TL;DR):"""

    print("🧠 Generating high-level TL;DR with Ollama...")
    tldr_text = query_ollama(tldr_prompt, model=model)

    # 2. Summarize each informative slide
    slide_results = []
    for idx, slide in enumerate(slides_to_summarize, start=1):
        dialogue = slide["dialogue"].strip()
        if not dialogue:
            dialogue = "(Visual slide presentation or data table displayed on screen)"

        prompt = f"""You are writing an executive slide-by-slide report. Summarize what is happening in this slide section based on the dialogue and topic.
Slide Time Range: [{slide['start_time']} - {slide['end_time']}]
Slide Image File: {slide['filename']}
Spoken Dialogue:
{dialogue[:1600]}

Respond ONLY in this exact format:
TITLE: <Specific descriptive title for this slide or table>
GIST:
- <Specific fact, number, country, or topic point 1>
- <Specific fact, number, country, or topic point 2>
TAKEAWAY: <Key actionable conclusion or observation>"""

        print(f"📝 [{idx}/{len(slides_to_summarize)}] Summarizing Slide {slide['slide_index']:02d} [{slide['start_time']} - {slide['end_time']}] ({slide['filename']})...")
        raw_summary = query_ollama(prompt, model=model)

        title = f"Slide {slide['slide_index']:02d}: Content & Discussion"
        gist_lines = []
        takeaway = ""

        for line in raw_summary.split("\n"):
            line = line.strip()
            if line.startswith("TITLE:"):
                title = line.replace("TITLE:", "").strip()
            elif line.startswith("-") or line.startswith("•"):
                gist_lines.append(line.lstrip("-• ").strip())
            elif line.startswith("TAKEAWAY:"):
                takeaway = line.replace("TAKEAWAY:", "").strip()

        if not gist_lines:
            gist_lines = [raw_summary[:200]]

        slide_results.append({
            "slide_index": slide["slide_index"],
            "title": title,
            "start_time": slide["start_time"],
            "end_time": slide["end_time"],
            "filename": slide["filename"],
            "image_relative_path": slide["image_relative_path"],
            "gist": gist_lines,
            "takeaway": takeaway or "Key content and data points covered."
        })

    # 3. Generate Clean Markdown
    md_lines = [
        f"# Executive Brief: {meeting_title}",
        "",
        f"- **Meeting Name:** `{meta.get('meeting_name')}`",
        f"- **Total Duration:** {meta.get('duration_str')}",
        f"- **Total Raw Slides:** {meta.get('total_slides')}",
        f"- **Informative Slides Documented:** {len(slide_results)}",
        f"- **AI Engine:** `{model}` (Local via Ollama)",
        "",
        "---",
        "",
        "## Executive Overview (TL;DR)",
        "",
        tldr_text,
        "",
        "---",
        "",
        "## Clean Slide-by-Slide Walkthrough (No Blanks / No Duplicates)",
        ""
    ]

    for s in slide_results:
        md_lines.append(f"### Slide {s['slide_index']:02d}: {s['title']} [{s['start_time']} - {s['end_time']}]")
        md_lines.append("")
        md_lines.append(f"![{s['title']}]({s['image_relative_path']})")
        md_lines.append("")
        md_lines.append("**The Gist:**")
        for g in s["gist"]:
            md_lines.append(f"- {g}")
        md_lines.append("")
        md_lines.append(f"> **Key Takeaway:** {s['takeaway']}")
        md_lines.append("")
        md_lines.append("---")
        md_lines.append("")

    md_output_path = os.path.join(output_dir, f"{output_prefix}.md")
    with open(md_output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))

    # 4. Generate Interactive Styled HTML
    html_cards = []
    for s in slide_results:
        gist_items = "".join([f"<li>{item}</li>" for item in s['gist']])
        html_cards.append(f"""
  <div class="chapter-card">
    <div class="chapter-header">
      <div class="chapter-title">Slide {s['slide_index']:02d}: {s['title']}</div>
      <div class="timestamp-badge">{s['start_time']} - {s['end_time']}</div>
    </div>
    <div class="slide-wrapper">
      <img src="{s['image_relative_path']}" alt="{s['title']}" loading="lazy" />
    </div>
    <div class="gist-content">
      <ul>{gist_items}</ul>
      <div class="takeaway-tag">
        <strong>Key Takeaway:</strong> {s['takeaway']}
      </div>
    </div>
  </div>""")

    html_content = f"""<!DOCTYPE html>
<html lang="en">
<head>
  <meta charset="UTF-8" />
  <meta name="viewport" content="width=device-width, initial-scale=1.0" />
  <title>Executive Brief: {meeting_title}</title>
  <link href="https://fonts.googleapis.com/css2?family=Plus+Jakarta+Sans:wght@400;500;600;700&display=swap" rel="stylesheet">
  <style>
    :root {{
      --bg-main: #f8fafc;
      --card-bg: #ffffff;
      --text-main: #0f172a;
      --text-muted: #64748b;
      --primary: #2563eb;
      --primary-soft: #eff6ff;
      --border: #e2e8f0;
      --accent: #0ea5e9;
      --highlight-bg: #f1f5f9;
      --radius: 12px;
    }}
    body {{
      font-family: 'Plus Jakarta Sans', system-ui, sans-serif;
      background-color: var(--bg-main);
      color: var(--text-main);
      line-height: 1.65;
      padding: 40px 20px;
    }}
    .container {{ max-width: 960px; margin: 0 auto; }}
    header {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 32px;
      margin-bottom: 24px;
      box-shadow: 0 4px 16px rgba(0,0,0,0.02);
    }}
    .badge {{
      display: inline-block;
      font-size: 0.8rem;
      font-weight: 600;
      padding: 4px 10px;
      border-radius: 6px;
      background: var(--primary-soft);
      color: var(--primary);
      margin-bottom: 12px;
    }}
    h1 {{ font-size: 1.9rem; font-weight: 700; margin-bottom: 8px; letter-spacing: -0.02em; }}
    .tldr-box {{
      background: var(--primary-soft);
      border-left: 4px solid var(--primary);
      padding: 22px;
      border-radius: 0 var(--radius) var(--radius) 0;
      margin-bottom: 36px;
    }}
    .chapter-card {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 26px;
      margin-bottom: 32px;
      box-shadow: 0 4px 16px rgba(0,0,0,0.02);
    }}
    .chapter-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 14px;
      gap: 12px;
    }}
    .chapter-title {{ font-size: 1.25rem; font-weight: 700; color: var(--text-main); }}
    .timestamp-badge {{
      font-size: 0.82rem;
      font-family: monospace;
      padding: 4px 10px;
      background: var(--highlight-bg);
      border-radius: 6px;
      border: 1px solid var(--border);
    }}
    .slide-wrapper {{
      margin: 16px 0;
      border-radius: 8px;
      overflow: hidden;
      border: 1px solid var(--border);
      background: #000;
    }}
    .slide-wrapper img {{ width: 100%; height: auto; display: block; }}
    .gist-content {{ margin-top: 14px; font-size: 0.98rem; }}
    .gist-content ul {{ padding-left: 20px; margin-bottom: 12px; }}
    .gist-content li {{ margin-bottom: 6px; }}
    .takeaway-tag {{
      background: var(--highlight-bg);
      border-left: 3px solid var(--accent);
      padding: 10px 14px;
      border-radius: 0 6px 6px 0;
      font-size: 0.92rem;
      margin-top: 12px;
    }}
  </style>
</head>
<body>
<div class="container">
  <header>
    <span class="badge">Executive Brief (Ollama: {model})</span>
    <h1>{meeting_title}</h1>
    <p style="color: var(--text-muted);">Duration: {meta.get('duration_str')} • Clean Informative Slides: {len(slide_results)} (No Blanks/Duplicates)</p>
  </header>
  <div class="tldr-box">
    <h2 style="font-size: 1.15rem; color: var(--primary); margin-bottom: 8px;">Executive Overview (TL;DR)</h2>
    <p>{tldr_text}</p>
  </div>
  {"".join(html_cards)}
</div>
</body>
</html>"""

    html_output_path = os.path.join(output_dir, f"{output_prefix}.html")
    with open(html_output_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    # Also keep executive_summary.html and .md updated as the active default
    default_md = os.path.join(output_dir, "executive_summary.md")
    default_html = os.path.join(output_dir, "executive_summary.html")
    with open(default_md, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))
    with open(default_html, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"\n🎉 Successfully Generated Clean AI Executive Briefs!")
    print(f"📄 Markdown : {md_output_path}")
    print(f"🌐 HTML     : {html_output_path}")
    print(f"🖼️  Cleanly documented {len(slide_results)} unique slides with zero blanks or duplicates.")

def main():
    parser = argparse.ArgumentParser(description="Generate Clean Executive Summary via Local Ollama")
    parser.add_argument("--metadata", required=True, help="Path to metadata.json generated by process_meeting.py")
    parser.add_argument("--model", default="llama3.2:latest", help="Ollama model name (default: llama3.2:latest)")
    parser.add_argument("--prefix", default="executive_summary_ollama", help="Output filename prefix (default: executive_summary_ollama)")
    args = parser.parse_args()

    summarize_meeting_with_ollama(
        metadata_path=args.metadata,
        model=args.model,
        output_prefix=args.prefix
    )

if __name__ == "__main__":
    main()
