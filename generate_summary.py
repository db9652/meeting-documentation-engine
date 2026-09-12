#!/usr/bin/env python3
"""
Automated Executive Summary Generator using Pure Dynamic Computer Vision + Local Ollama
1. Computer Vision (Laplacian): Detects and eliminates blank/empty slides.
2. Computer Vision (Additive Build & Directional Edge Containment): Automatically detects and
   collapses partial-build slides (e.g. Slide 08 into Table 1 Slide 09, Slide 10/11 into Table 2 Slide 12,
   and Slide 40/41/42 into complete Recommendations Slide 43) dynamically on ANY meeting video.
3. Speech Accumulator: Carries forward all spoken dialogue from partial/blank slides into the fully-filled slide.
4. Local LLM Synthesizer (Ollama): Generates structured gists, takeaways, and TL;DR using llama3.2.
"""

import os
import json
import cv2
import numpy as np
import argparse
import urllib.request
import urllib.error

def query_ollama(prompt: str, model: str = "llama3.2:latest", host: str = "http://localhost:11434", json_mode: bool = False) -> str:
    """Sends a generation request to the local Ollama instance."""
    url = f"{host}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.1 if json_mode else 0.2,
            "num_ctx": 8192
        }
    }
    if json_mode:
        payload["format"] = "json"

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

def is_blank_slide_cv(img_path: str, threshold: float = 1000.0) -> bool:
    """Detects whether an image is a blank/empty background slide or transition wipe using Laplacian variance."""
    if not os.path.exists(img_path):
        return True
    img = cv2.imread(img_path)
    if img is None:
        return True
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    lap_var = cv2.Laplacian(gray, cv2.CV_64F).var()
    return lap_var < threshold

def get_slide_content_edges(img_path: str):
    """Extracts the central presentation canvas (excluding webcams/player chrome) and computes Canny edges."""
    img = cv2.imread(img_path)
    if img is None:
        return None
    gray = cv2.cvtColor(img, cv2.COLOR_BGR2GRAY)
    h, w = gray.shape
    # Central content ROI: isolates the slide canvas from conference UI, participant thumbnails, and controls
    roi = gray[int(h*0.12):int(h*0.88), int(w*0.08):int(w*0.82)]
    return cv2.Canny(roi, 50, 150)

def is_partial_build_cv(edges_a, edges_b):
    """
    Pure Computer Vision test to determine if edges_a is a partial build or superseded version of edges_b.
    1. Directional Edge Containment: Dilates edges_b with a 5x5 kernel (+/- 2 pixels) to accommodate
       compression artifacts and sub-pixel text rendering shifts.
    2. Additive Build: If >= 70% of edges_a are contained inside edges_b AND edges_b contains
       at least 20% more visual edge content (e.g., Table rows added or bullet points revealed).
    3. Completion / Near-Identical: If >= 90% of edges_a are contained in edges_b and edges_b is
       the settled, complete state.
    """
    cnt_a = np.sum(edges_a > 0)
    cnt_b = np.sum(edges_b > 0)
    if cnt_a == 0:
        return True, 1.0, "empty_canvas"
    
    kernel = np.ones((5,5), np.uint8)
    edges_b_dil = cv2.dilate(edges_b, kernel)
    containment = np.sum((edges_a > 0) & (edges_b_dil > 0)) / cnt_a
    
    # Case 1: Additive build (e.g. Table revealed or bullets appended)
    if containment >= 0.70 and cnt_b >= 1.20 * cnt_a:
        return True, containment, f"additive_build (edges {cnt_a} -> {cnt_b})"
        
    # Case 2: Settled completion / near-identical transition
    if containment >= 0.90 and cnt_b >= 0.85 * cnt_a:
        return True, containment, f"completion (edges {cnt_a} -> {cnt_b})"
        
    return False, containment, "distinct"

def select_fully_filled_slides(sections, base_dir: str, filter_mode: str = "cv", model: str = "llama3.2:latest") -> list:
    """
    Universal Dynamic Slide Filter:
    1. Stage 1 (CV Blank Filter): Drops empty canvas / transition frames via Laplacian edge variance.
    2. Stage 2 (CV Additive Build Filter): Evaluates consecutive non-blank slides. If slide A's visual
       content is a subset of a subsequent slide B, slide A is collapsed into slide B.
    3. Optional Hybrid Mode: If filter_mode == 'hybrid', local LLM provides an additional semantic review
       using generic presentation rules without any hardcoded slide numbers.
    4. Dialogue & Timing Accumulator: Automatically merges dialogue and extends timestamps from
       eliminated/merged slides into the final fully-filled slide so no spoken dialogue is lost.
    """
    # Step 1: Filter out blank frames
    non_blank_sections = []
    accumulated_blank_text = ""

    for s in sections:
        img_path = os.path.join(base_dir, s["image_relative_path"])
        if is_blank_slide_cv(img_path):
            accumulated_blank_text += " " + s.get("dialogue_text", "")
        else:
            if accumulated_blank_text:
                s["dialogue_text"] = (accumulated_blank_text + " " + s.get("dialogue_text", "")).strip()
                accumulated_blank_text = ""
            non_blank_sections.append(s)

    if accumulated_blank_text and non_blank_sections:
        non_blank_sections[-1]["dialogue_text"] = (
            non_blank_sections[-1].get("dialogue_text", "") + " " + accumulated_blank_text
        ).strip()

    print(f"   • CV Blank Filter: Retained {len(non_blank_sections)} non-blank candidate slides from {len(sections)} raw frames.")

    # Step 2: Computer Vision Additive Build Detection
    edge_cache = {}
    for s in non_blank_sections:
        img_path = os.path.join(base_dir, s["image_relative_path"])
        edge_cache[s["slide_index"]] = get_slide_content_edges(img_path)

    cv_keep_indices = set()
    i = 0
    while i < len(non_blank_sections):
        curr = non_blank_sections[i]
        c_idx = curr["slide_index"]
        c_edges = edge_cache[c_idx]
        
        superseded = False
        # Look ahead up to 2 slides within non-blank sequence, or within 60s
        for lookahead in range(1, min(3, len(non_blank_sections) - i)):
            nxt = non_blank_sections[i + lookahead]
            time_diff = nxt.get("start_sec", 0) - curr.get("start_sec", 0)
            if time_diff > 60:
                continue
            n_idx = nxt["slide_index"]
            n_edges = edge_cache[n_idx]
            
            is_sub, cont, reason = is_partial_build_cv(c_edges, n_edges)
            if is_sub:
                print(f"   • [CV Collapse] Slide {c_idx:02d} superseded by Slide {n_idx:02d} (containment={cont*100:.1f}%, {reason})")
                superseded = True
                break
                
        if not superseded:
            cv_keep_indices.add(c_idx)
        i += 1

    print(f"   • CV Build Collapser: Filtered down to {len(cv_keep_indices)} fully-filled slides.")

    # Always retain first and last slide
    if non_blank_sections:
        cv_keep_indices.add(non_blank_sections[0]["slide_index"])
        cv_keep_indices.add(non_blank_sections[-1]["slide_index"])

    keep_indices = cv_keep_indices

    # Step 3: Optional Hybrid verification with Ollama
    if filter_mode == "hybrid":
        print("   • Hybrid Verification: Querying Ollama for semantic validation...")
        slide_desc = []
        for s in non_blank_sections:
            if s["slide_index"] in cv_keep_indices:
                text = s.get("dialogue_text", "")[:100] if s.get("dialogue_text") else "(Visual presentation)"
                slide_desc.append(f"Slide {s['slide_index']:02d} [{s['start_time_str']} - {s['end_time_str']}]: {text}")
        
        timeline_text = "\n".join(slide_desc)
        hybrid_prompt = f"""You are an executive meeting documentation assistant.
Review the following candidate slides and timestamps. Verify which slides contain distinct, meaningful topics or final complete tables.
Return a JSON object with the list of slide numbers to keep.
Example format: {{"keep_slides": [1, 3, 5, 9]}}

Slide Timeline:
{timeline_text}
"""
        try:
            llm_res = query_ollama(hybrid_prompt, model=model, json_mode=True)
            llm_json = json.loads(llm_res)
            llm_slides = set(llm_json.get("keep_slides", []))
            if llm_slides:
                keep_indices = keep_indices.intersection(llm_slides)
                keep_indices.add(non_blank_sections[0]["slide_index"])
                keep_indices.add(non_blank_sections[-1]["slide_index"])
        except Exception as e:
            print(f"   ⚠️  Warning: Hybrid LLM review skipped ({e}). Using CV filter.")

    # Step 4: Cluster dialogue and consolidate timestamps into the selected fully-filled slides
    final_slides = []
    current_cluster = []

    for s in non_blank_sections:
        current_cluster.append(s)
        if s["slide_index"] in keep_indices:
            combined_dialogue = " ".join([item.get("dialogue_text", "") for item in current_cluster if item.get("dialogue_text")]).strip()
            start_time = current_cluster[0]["start_time_str"]
            end_time = s["end_time_str"]

            final_slides.append({
                "slide_index": s["slide_index"],
                "start_time": start_time,
                "end_time": end_time,
                "filename": s["image_filename"],
                "image_relative_path": s["image_relative_path"],
                "dialogue": combined_dialogue,
                "duration_sec": round(s.get("end_sec", 0) - current_cluster[0].get("start_sec", 0), 1)
            })
            current_cluster = []

    if current_cluster and final_slides:
        leftover_text = " ".join([item.get("dialogue_text", "") for item in current_cluster if item.get("dialogue_text")]).strip()
        if leftover_text:
            final_slides[-1]["dialogue"] = (final_slides[-1]["dialogue"] + " " + leftover_text).strip()

    return final_slides

def summarize_meeting_with_ollama(
    metadata_path: str,
    model: str = "llama3.2:latest",
    output_prefix: str = "executive_summary_ollama",
    filter_mode: str = "cv"
):
    with open(metadata_path, "r", encoding="utf-8") as f:
        meta = json.load(f)

    meeting_title = meta.get("meeting_title", "Meeting Brief")
    sections = meta.get("sections", [])
    output_dir = os.path.dirname(os.path.abspath(metadata_path))

    print("=" * 70)
    print(f"🚀 Running Pure Dynamic CV + Local LLM Meeting Summarizer (Fully-Filled Slides)")
    print(f"📌 Meeting Title : {meeting_title}")
    print(f"👁️  Filter Engine : Pure Dynamic Computer Vision (mode: {filter_mode})")
    print(f"🤖 Local LLM     : {model} via Ollama")
    print("=" * 70)

    print("\n🔍 Step 1: Performing Pure Dynamic CV Filtering (Blank Elimination + Additive Build Collapsing)...")
    slides_to_summarize = select_fully_filled_slides(sections, base_dir=output_dir, filter_mode=filter_mode, model=model)
    print(f"✅ Retained {len(slides_to_summarize)} fully-filled slides with zero hardcoded indices.")

    # 1. Full meeting TL;DR
    all_dialogue = " ".join([s.get("dialogue_text", "") for s in sections])
    tldr_prompt = f"""You are an executive business analyst. Read the following meeting transcript and write a concise, professional 3-4 sentence Executive Summary (TL;DR).
Summarize the core topic, key findings across countries, and main conclusions directly without saying "the speaker said".

Meeting Title: {meeting_title}
Transcript snippet:
{all_dialogue[:4500]}

Executive Summary (TL;DR):"""

    print("\n🧠 Step 2: Generating Executive TL;DR with Ollama...")
    tldr_text = query_ollama(tldr_prompt, model=model)

    # 2. Summarize each fully-filled slide
    slide_results = []
    print("\n📝 Step 3: Summarizing each fully-filled slide...")
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

        print(f"   [{idx:02d}/{len(slides_to_summarize):02d}] Slide {slide['slide_index']:02d} [{slide['start_time']} - {slide['end_time']}] -> {slide['filename']}...")
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
        f"- **Fully-Filled Slides Documented:** {len(slide_results)}",
        f"- **Filter Engine:** Pure Dynamic Computer Vision (Zero Hardcoding)",
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
        "## Clean Slide-by-Slide Walkthrough (Fully-Filled Slides Only)",
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
    <span class="badge">Executive Brief (Ollama: {model} • Pure Dynamic Computer Vision Filter)</span>
    <h1>{meeting_title}</h1>
    <p style="color: var(--text-muted);">Duration: {meta.get('duration_str')} • Fully-Filled Slides: {len(slide_results)} (Zero Blanks / Zero Partial Builds)</p>
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

    # Output files written via output_prefix

    print("\n" + "=" * 70)
    print("🎉 Successfully Generated Purely Dynamic AI Executive Briefs!")
    print(f"📄 Markdown : {md_output_path}")
    print(f"🌐 HTML     : {html_output_path}")
    print(f"🖼️  Documented {len(slide_results)} fully-filled slides with zero hardcoded indices.")
    print("=" * 70)

def main():
    parser = argparse.ArgumentParser(description="Generate Executive Summary via Pure Dynamic CV + Local Ollama")
    parser.add_argument("--metadata", required=True, help="Path to metadata.json generated by process_meeting.py")
    parser.add_argument("--model", default="llama3.2:latest", help="Ollama model name (default: llama3.2:latest)")
    parser.add_argument("--prefix", default="executive_summary_ollama", help="Output filename prefix (default: executive_summary_ollama)")
    parser.add_argument("--filter-mode", default="cv", choices=["cv", "hybrid"], help="Slide filter mode: 'cv' (Pure Dynamic Computer Vision) or 'hybrid' (CV + LLM check)")
    args = parser.parse_args()

    summarize_meeting_with_ollama(
        metadata_path=args.metadata,
        model=args.model,
        output_prefix=args.prefix,
        filter_mode=args.filter_mode
    )

if __name__ == "__main__":
    main()
