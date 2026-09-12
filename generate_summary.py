#!/usr/bin/env python3
"""
Automated Executive Summary Generator using Local Ollama (Phase 1 Extension)
Reads metadata.json from process_meeting.py, groups micro-slides into major chapters,
calls a local lightweight LLM (e.g. llama3.2), and generates executive_summary.md and .html.
"""

import os
import json
import argparse
import urllib.request
import urllib.error

def query_ollama(prompt: str, model: str = "llama3.2:latest", host: str = "http://localhost:11434") -> str:
    """Sends a generation request to the local Ollama instance."""
    url = f"{host}/api/generate"
    payload = {
        "model": model,
        "prompt": prompt,
        "stream": False,
        "options": {
            "temperature": 0.3,
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

def group_sections_into_chapters(sections, target_chapters: int = 7):
    """
    Groups granular micro-slides (e.g. 45 slides) into a manageable number of
    thematic chapters based on dialogue density and time progression.
    """
    total_sections = len(sections)
    if total_sections <= target_chapters:
        return [[s] for s in sections]

    # Calculate average number of sections per chapter
    step = total_sections / target_chapters
    chapters = []
    
    for i in range(target_chapters):
        start_idx = int(round(i * step))
        end_idx = int(round((i + 1) * step))
        if i == target_chapters - 1:
            end_idx = total_sections
            
        group = sections[start_idx:end_idx]
        if group:
            chapters.append(group)
            
    return chapters

def summarize_meeting_with_ollama(metadata_path: str, model: str = "llama3.2:latest", target_chapters: int = 7, output_prefix: str = "executive_summary_ollama"):
    with open(metadata_path, "r", encoding="utf-8") as f:
        meta = json.load(f)

    meeting_title = meta.get("meeting_title", "Meeting Brief")
    sections = meta.get("sections", [])
    output_dir = os.path.dirname(os.path.abspath(metadata_path))

    print(f"📋 Reading metadata from: {metadata_path}")
    print(f"🤖 Connecting to local Ollama (Model: {model})")
    print(f"📊 Aggregating {len(sections)} micro-slides into {target_chapters} executive chapters...")

    # 1. Full meeting TL;DR
    all_dialogue = " ".join([s.get("dialogue_text", "") for s in sections])
    tldr_prompt = f"""You are an executive business analyst. Read the following spoken meeting transcript and write a concise 3-4 sentence Executive Summary (TL;DR).
Do not mention "in this transcript" or "the speaker said". Summarize the core topic, key findings, and main conclusions directly.

Meeting Title: {meeting_title}
Transcript snippet:
{all_dialogue[:4000]}

Executive Summary (TL;DR):"""

    print("🧠 Generating high-level TL;DR with Ollama...")
    tldr_text = query_ollama(tldr_prompt, model=model)

    # 2. Summarize each chapter
    grouped_chapters = group_sections_into_chapters(sections, target_chapters=target_chapters)
    chapter_results = []

    for idx, chapter in enumerate(grouped_chapters, start=1):
        # Pick the most complete slide in this group (usually the last slide in the group, or the one with longest duration)
        rep_slide = chapter[-1]
        
        start_time = chapter[0]["start_time_str"]
        end_time = chapter[-1]["end_time_str"]
        chapter_dialogue = " ".join([s.get("dialogue_text", "") for s in chapter if s.get("dialogue_text")])

        if not chapter_dialogue.strip():
            chapter_dialogue = "(Visual transition or slide demonstration)"

        prompt = f"""You are writing an executive meeting report. Summarize the following chapter from a presentation.
Time Range: [{start_time} - {end_time}]
Spoken Dialogue:
{chapter_dialogue[:2000]}

Respond ONLY in this exact structured format:
TITLE: <Clear professional title for this chapter>
GIST:
- <Key point 1>
- <Key point 2>
- <Key point 3>
TAKEAWAY: <One bold actionable conclusion or insight>"""

        print(f"📝 Summarizing Chapter {idx}/{len(grouped_chapters)} [{start_time} - {end_time}]...")
        raw_summary = query_ollama(prompt, model=model)

        # Parse the structured response
        title = f"Chapter {idx}: Discussion"
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

        chapter_results.append({
            "chapter_index": idx,
            "title": title,
            "start_time": start_time,
            "end_time": end_time,
            "image_relative_path": rep_slide["image_relative_path"],
            "gist": gist_lines,
            "takeaway": takeaway or "Key insights and discussion points covered."
        })

    # 3. Generate Markdown
    md_lines = [
        f"# Executive Brief: {meeting_title}",
        "",
        f"- **Meeting Name:** `{meta.get('meeting_name')}`",
        f"- **Total Duration:** {meta.get('duration_str')}",
        f"- **Processed Slides:** {meta.get('total_slides')}",
        f"- **AI Model:** `{model}` (Local via Ollama)",
        "",
        "---",
        "",
        "## Executive Overview (TL;DR)",
        "",
        tldr_text,
        "",
        "---",
        "",
        "## Chapter Walkthrough",
        ""
    ]

    for c in chapter_results:
        md_lines.append(f"### {c['chapter_index']}. {c['title']} [{c['start_time']} - {c['end_time']}]")
        md_lines.append("")
        md_lines.append(f"![{c['title']}]({c['image_relative_path']})")
        md_lines.append("")
        md_lines.append("**The Gist:**")
        for g in c["gist"]:
            md_lines.append(f"- {g}")
        md_lines.append("")
        md_lines.append(f"> **Key Takeaway:** {c['takeaway']}")
        md_lines.append("")
        md_lines.append("---")
        md_lines.append("")

    md_output_path = os.path.join(output_dir, f"{output_prefix}.md")
    with open(md_output_path, "w", encoding="utf-8") as f:
        f.write("\n".join(md_lines))

    # 4. Generate Interactive HTML
    html_cards = []
    for c in chapter_results:
        gist_items = "".join([f"<li>{item}</li>" for item in c['gist']])
        html_cards.append(f"""
  <div class="chapter-card">
    <div class="chapter-header">
      <div class="chapter-title">{c['chapter_index']}. {c['title']}</div>
      <div class="timestamp-badge">{c['start_time']} - {c['end_time']}</div>
    </div>
    <div class="slide-wrapper">
      <img src="{c['image_relative_path']}" alt="{c['title']}" loading="lazy" />
    </div>
    <div class="gist-content">
      <ul>{gist_items}</ul>
      <div class="takeaway-tag">
        <strong>Key Takeaway:</strong> {c['takeaway']}
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
    .container {{ max-width: 920px; margin: 0 auto; }}
    header {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 32px;
      margin-bottom: 24px;
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
    h1 {{ font-size: 1.85rem; font-weight: 700; margin-bottom: 8px; }}
    .tldr-box {{
      background: var(--primary-soft);
      border-left: 4px solid var(--primary);
      padding: 20px;
      border-radius: 0 var(--radius) var(--radius) 0;
      margin-bottom: 32px;
    }}
    .chapter-card {{
      background: var(--card-bg);
      border: 1px solid var(--border);
      border-radius: var(--radius);
      padding: 24px;
      margin-bottom: 28px;
    }}
    .chapter-header {{
      display: flex;
      justify-content: space-between;
      align-items: center;
      margin-bottom: 14px;
    }}
    .chapter-title {{ font-size: 1.2rem; font-weight: 700; }}
    .timestamp-badge {{
      font-size: 0.82rem;
      font-family: monospace;
      padding: 4px 8px;
      background: var(--highlight-bg);
      border-radius: 6px;
    }}
    .slide-wrapper {{
      margin: 16px 0;
      border-radius: 8px;
      overflow: hidden;
      border: 1px solid var(--border);
    }}
    .slide-wrapper img {{ width: 100%; display: block; }}
    .takeaway-tag {{
      background: var(--highlight-bg);
      border-left: 3px solid var(--accent);
      padding: 10px 14px;
      margin-top: 12px;
      border-radius: 0 6px 6px 0;
    }}
  </style>
</head>
<body>
<div class="container">
  <header>
    <span class="badge">AI Executive Brief (Ollama: {model})</span>
    <h1>{meeting_title}</h1>
    <p style="color: var(--text-muted);">Duration: {meta.get('duration_str')} • Total Slides: {meta.get('total_slides')}</p>
  </header>
  <div class="tldr-box">
    <h2 style="font-size: 1.1rem; color: var(--primary); margin-bottom: 6px;">Executive Overview (TL;DR)</h2>
    <p>{tldr_text}</p>
  </div>
  {"".join(html_cards)}
</div>
</body>
</html>"""

    html_output_path = os.path.join(output_dir, f"{output_prefix}.html")
    with open(html_output_path, "w", encoding="utf-8") as f:
        f.write(html_content)

    print(f"\n🎉 Successfully Generated AI Executive Briefs!")
    print(f"📄 Markdown : {md_output_path}")
    print(f"🌐 HTML     : {html_output_path}")

def main():
    parser = argparse.ArgumentParser(description="Generate Executive Summary via Local Ollama")
    parser.add_argument("--metadata", required=True, help="Path to metadata.json generated by process_meeting.py")
    parser.add_argument("--model", default="llama3.2:latest", help="Ollama model name (default: llama3.2:latest)")
    parser.add_argument("--chapters", type=int, default=7, help="Number of executive chapters to produce (default: 7)")
    parser.add_argument("--prefix", default="executive_summary_ollama", help="Output filename prefix (default: executive_summary_ollama)")
    args = parser.parse_args()

    summarize_meeting_with_ollama(
        metadata_path=args.metadata,
        model=args.model,
        target_chapters=args.chapters,
        output_prefix=args.prefix
    )

if __name__ == "__main__":
    main()
